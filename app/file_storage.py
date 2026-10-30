"""Private per-client assets. File bytes are never exposed by the static server."""
import hashlib
import os
import re
import secrets
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

MAX_UPLOAD = int(os.environ.get('EVOOLUI_MAX_UPLOAD_MB', '250')) * 1024 * 1024
CHUNK = 64 * 1024
CLIENT_QUOTA = int(os.environ.get('EVOOLUI_CLIENT_STORAGE_MB', '5120')) * 1024 * 1024
MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.gif': 'image/gif', '.webp': 'image/webp', '.mp4': 'video/mp4',
         '.mov': 'video/quicktime', '.webm': 'video/webm', '.pdf': 'application/pdf',
         '.txt': 'text/plain', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


class FileError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def initialize(db):
    # Additive schema migration; existing clients, logins and business records stay intact.
    db.executescript('''
    CREATE TABLE IF NOT EXISTS files(
        id TEXT PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id),
        parent_id TEXT UNIQUE REFERENCES files(id), version INTEGER NOT NULL,
        name TEXT NOT NULL, storage_key TEXT UNIQUE NOT NULL, mime TEXT NOT NULL,
        size INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_by INTEGER NOT NULL REFERENCES users(id),
        status TEXT NOT NULL DEFAULT 'pendente', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS files_by_client ON files(client_id, created_at);
    CREATE TABLE IF NOT EXISTS file_reviews(
        id INTEGER PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id),
        user_id INTEGER NOT NULL REFERENCES users(id), decision TEXT NOT NULL,
        comment TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    if 'task_id' not in {r[1] for r in db.execute('PRAGMA table_info(files)')}:
        db.execute('ALTER TABLE files ADD COLUMN task_id INTEGER REFERENCES tasks(id)')


def root_for(db_path):
    return Path(os.environ.get('EVOOLUI_FILES_DIR', str(Path(db_path).parent / 'private_files'))).resolve()


def authorize(h, db, user, client_id, write=False):
    if client_id not in h.visible(db, user):
        raise FileError('Pasta ou arquivo não encontrado.', 404)
    if write and user['role'] not in ('administrador', 'socio', 'funcionario'):
        raise FileError('Somente a equipe autorizada pode enviar arquivos.', 403)


def record(h, db, user, fid, write=False):
    if not re.fullmatch(r'[a-f0-9]{32}', fid):
        raise FileError('Arquivo não encontrado.', 404)
    row = db.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
    if row is None:
        raise FileError('Arquivo não encontrado.', 404)
    authorize(h, db, user, row['client_id'], write)
    return row


def metadata(db, row):
    data = {k: row[k] for k in ('id', 'client_id', 'parent_id', 'version', 'name', 'mime',
                                'size', 'sha256', 'uploaded_by', 'status', 'created_at', 'task_id')}
    data['uploader'] = db.execute('SELECT name FROM users WHERE id=?', (row['uploaded_by'],)).fetchone()[0]
    return data


def history(db, row):
    # Parent links cannot cross tenants and each version has at most one child.
    first = row
    while first['parent_id']:
        first = db.execute('SELECT * FROM files WHERE id=?', (first['parent_id'],)).fetchone()
    versions = []
    while first:
        item = metadata(db, first)
        item['reviews'] = [dict(r) for r in db.execute('''
            SELECT r.id,r.decision,r.comment,r.created_at,u.name AS reviewer
            FROM file_reviews r JOIN users u ON u.id=r.user_id WHERE r.file_id=? ORDER BY r.id
        ''', (first['id'],))]
        versions.append(item)
        first = db.execute('SELECT * FROM files WHERE parent_id=?', (first['id'],)).fetchone()
    return versions


def detect(path, name):
    ext = Path(name).suffix.lower()
    if ext not in MIMES:
        raise FileError('Formato não permitido. Use imagens, MP4/MOV/WebM, PDF, TXT, DOCX, XLSX ou PPTX.', 415)
    with path.open('rb') as f:
        head = f.read(512)
    valid = False
    if ext == '.png': valid = head.startswith(b'\x89PNG\r\n\x1a\n')
    elif ext in ('.jpg', '.jpeg'): valid = head.startswith(b'\xff\xd8\xff')
    elif ext == '.gif': valid = head.startswith((b'GIF87a', b'GIF89a'))
    elif ext == '.webp': valid = head[:4] == b'RIFF' and head[8:12] == b'WEBP'
    elif ext in ('.mp4', '.mov'):
        valid = head[4:8] == b'ftyp' or (ext == '.mov' and head[4:8] in (b'moov', b'mdat', b'wide'))
    elif ext == '.webm': valid = head.startswith(b'\x1aE\xdf\xa3') and b'webm' in head.lower()
    elif ext == '.pdf': valid = head.startswith(b'%PDF-')
    elif ext == '.txt':
        # Stream decoding, rather than loading a large file into memory.
        try:
            with path.open(encoding='utf-8') as f:
                for block in iter(lambda: f.read(CHUNK), ''):
                    if any(ord(c) < 32 and c not in '\n\r\t' for c in block):
                        raise ValueError()
            valid = True
        except (UnicodeError, ValueError): pass
    else:
        main = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}[ext]
        try:
            with zipfile.ZipFile(path) as z:
                entries = z.infolist()
                names = {e.filename for e in entries}
                valid = (len(entries) < 10000 and sum(e.file_size for e in entries) <= 1024**3
                         and '[Content_Types].xml' in names and main in names
                         and not any('vbaproject' in n.lower() for n in names))
        except (zipfile.BadZipFile, OSError): pass
    if not valid:
        raise FileError('O conteúdo não corresponde ao formato informado.', 415)
    return MIMES[ext]


def upload(h, db, user, cid, storage_root, audit, parent=None):
    authorize(h, db, user, cid, write=True)
    q = parse_qs(urlparse(h.path).query)
    name = q.get('name', [''])[0].strip()
    if (not name or len(name) > 180 or '/' in name or '\\' in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise FileError('Nome de arquivo inválido.')
    if Path(name).suffix.lower() not in MIMES:
        raise FileError('Formato não permitido.', 415)
    if h.headers.get('Transfer-Encoding'):
        raise FileError('Envie o arquivo com tamanho definido.', 411)
    try:
        length = int(h.headers['Content-Length'])
    except (TypeError, ValueError):
        raise FileError('Informe o tamanho do arquivo.', 411)
    if not 0 < length <= MAX_UPLOAD:
        raise FileError(f'O arquivo deve ter entre 1 byte e {MAX_UPLOAD // (1024*1024)} MB.', 413)
    if db.execute('SELECT COALESCE(sum(size),0) FROM files WHERE client_id=?',(cid,)).fetchone()[0] + length > CLIENT_QUOTA:
        raise FileError('A pasta atingiu o limite de armazenamento configurado.',413)
    storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(storage_root, 0o700)
    folder = storage_root / str(cid)
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise FileError('Armazenamento indisponível.', 503)
    os.chmod(folder, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=folder)
    temporary = Path(temporary)
    final = None
    committed = False
    try:
        sha = hashlib.sha256()
        remaining = length
        with os.fdopen(fd, 'wb') as target:
            h.connection.settimeout(120)
            while remaining:
                try:
                    block = h.rfile.read(min(CHUNK, remaining))
                except TimeoutError:
                    raise FileError('Tempo de envio esgotado. Atualize a pasta antes de tentar novamente.',408)
                if not block:
                    raise FileError('Envio interrompido; tente novamente.')
                target.write(block); sha.update(block); remaining -= len(block)
            target.flush(); os.fsync(target.fileno())
        mime = detect(temporary, name)
        db.execute('BEGIN IMMEDIATE')
        # Recheck session/assignment after a potentially long transfer.
        user = h.session(db)
        authorize(h, db, user, cid, write=True)
        if db.execute('SELECT COALESCE(sum(size),0) FROM files WHERE client_id=?',(cid,)).fetchone()[0] + length > CLIENT_QUOTA:
            raise FileError('A pasta atingiu o limite de armazenamento configurado.',413)
        version = 1
        if parent:
            previous = record(h, db, user, parent, write=True)
            if previous['client_id'] != cid or db.execute('SELECT 1 FROM files WHERE parent_id=?', (parent,)).fetchone():
                raise FileError('Já existe uma versão mais recente. Atualize a pasta.', 409)
            version = previous['version'] + 1
        fid = secrets.token_hex(16)
        key = secrets.token_hex(24) + '.bin'
        final = folder / key
        os.replace(temporary, final)
        db.execute('''INSERT INTO files(id,client_id,parent_id,version,name,storage_key,mime,size,sha256,uploaded_by)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (fid, cid, parent, version, name, key, mime, length, sha.hexdigest(), user['id']))
        from app import operations
        try:
            task_id = int(q['task_id'][0]) if q.get('task_id', [''])[0] else None
        except ValueError:
            raise FileError('Tarefa inválida.')
        operations.on_file(db, user, fid, task_id, parent)
        audit(db, user, 'enviar_versao' if parent else 'enviar_arquivo', 'files', fid)
        db.commit(); committed = True
        h.reply({'file': metadata(db, db.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone())}, 201)
    finally:
        temporary.unlink(missing_ok=True)
        if final and not committed:
            final.unlink(missing_ok=True)


def review(h, db, user, fid, audit):
    if user['role'] != 'cliente':
        raise FileError('A aprovação deve ser feita pelo cliente da empresa.', 403)
    data = h.body()
    decision = data.get('decision')
    comment = data.get('comment', '')
    if decision not in ('aprovado', 'alteracoes_solicitadas') or not isinstance(comment, str) or len(comment) > 3000:
        raise FileError('Decisão ou observação inválida.')
    comment = comment.strip()
    if decision == 'alteracoes_solicitadas' and not comment:
        raise FileError('Descreva as alterações que você precisa.')
    db.execute('BEGIN IMMEDIATE')
    row = record(h, db, user, fid)
    if row['status'] != 'pendente' or db.execute('SELECT 1 FROM files WHERE parent_id=?', (fid,)).fetchone():
        raise FileError('Esta versão já foi avaliada ou substituída. Atualize a pasta.', 409)
    db.execute('INSERT INTO file_reviews(file_id,user_id,decision,comment) VALUES(?,?,?,?)',
               (fid, user['id'], decision, comment))
    db.execute('UPDATE files SET status=? WHERE id=?', (decision, fid))
    from app import operations
    operations.on_review(db, row, decision, comment)
    audit(db, user, decision, 'files', fid)
    db.commit()
    h.reply({'ok': True, 'status': decision})


def serve_bytes(h, db, user, row, root, download, audit):
    path = root / str(row['client_id']) / row['storage_key']
    # Enforce containment even if the storage tree is misconfigured.
    if path.resolve().parent != (root / str(row['client_id'])) or path.is_symlink():
        raise FileError('Armazenamento indisponível.', 503)
    try:
        source = path.open('rb')
    except FileNotFoundError:
        raise FileError('Arquivo indisponível no armazenamento.', 404)
    with source:
        size = os.fstat(source.fileno()).st_size
        start, end, partial = 0, size - 1, False
        range_header = h.headers.get('Range')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or not any(match.groups()) or len(range_header)>128:
                h.send_response(416); h.headers_out('application/octet-stream', 0)
                h.send_header('Content-Range', f'bytes */{size}'); h.end_headers(); return
            a, b = match.groups()
            if a:
                start = int(a); end = min(int(b), size-1) if b else size-1
            else:
                start = max(0, size-int(b)); end = size-1
            if start >= size or start > end or (not a and int(b) == 0):
                h.send_response(416); h.headers_out('application/octet-stream', 0)
                h.send_header('Content-Range', f'bytes */{size}'); h.end_headers(); return
            partial = True
        if h.command == 'GET':
            audit(db, user, 'download' if download else 'visualizar_arquivo', 'files', row['id'])
            db.commit()
        inline = not download and row['mime'].startswith(('image/', 'video/'))
        h.send_response(206 if partial else 200)
        h.headers_out(row['mime'] if inline else 'application/octet-stream', end-start+1)
        h.send_header('Accept-Ranges', 'bytes')
        h.send_header('Content-Disposition', ('inline' if inline else 'attachment') + "; filename=arquivo" + Path(row['name']).suffix.lower() + "; filename*=UTF-8''" + quote(row['name'], safe=''))
        if partial: h.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        h.end_headers()
        if h.command == 'HEAD': return
        source.seek(start); remaining = end-start+1
        try:
            while remaining:
                block = source.read(min(CHUNK, remaining))
                if not block: break
                h.wfile.write(block); remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError): pass


def handle(h, db, user, path, db_path, audit):
    collection = re.fullmatch(r'/api/clients/(\d+)/files', path)
    if collection:
        cid = int(collection[1]); authorize(h, db, user, cid)
        if h.command == 'GET':
            rows = db.execute('''SELECT f.* FROM files f WHERE f.client_id=?
                AND NOT EXISTS(SELECT 1 FROM files c WHERE c.parent_id=f.id)
                ORDER BY f.created_at DESC,f.rowid DESC''', (cid,))
            h.reply({'files': [metadata(db, r) for r in rows], 'max_upload_bytes': MAX_UPLOAD})
        elif h.command == 'POST': upload(h, db, user, cid, root_for(db_path), audit)
        else: raise FileError('Método não permitido.', 405)
        return
    match = re.fullmatch(r'/api/files/([^/]+)(?:/(content|download|reviews|versions))?', path)
    if not match: raise FileError('Não encontrado.', 404)
    fid, action = match.groups()
    row = record(h, db, user, fid)
    if action is None and h.command == 'GET':
        h.reply({'file': metadata(db, row), 'versions': history(db, row)})
    elif action in ('content', 'download') and h.command in ('GET', 'HEAD'):
        serve_bytes(h, db, user, row, root_for(db_path), action == 'download', audit)
    elif action == 'reviews' and h.command == 'POST': review(h, db, user, fid, audit)
    elif action == 'versions' and h.command == 'POST':
        upload(h, db, user, row['client_id'], root_for(db_path), audit, parent=fid)
    else: raise FileError('Método não permitido.', 405)
