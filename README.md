# EVOOLUI GESTÃO

Aplicação funcional de gestão de agência, com a primeira etapa preservada e novos módulos de arquivos privados, aprovações, agendamentos, automações internas e financeiro. Backend Python, banco SQLite em disco e interface responsiva em português, sem dependências de execução de terceiros. Não utiliza armazenamento do navegador como banco de dados.

## Executar localmente

Requer Python 3.12+ com SQLite e base de fusos horários (disponíveis neste ambiente). Use o checkout existente `/workspace/site-evoolui`; cada tarefa na nuvem já é isolada, não é necessário criar um worktree.

```bash
cd /workspace/site-evoolui
python3 -m py_compile app/server.py
# Escolha uma senha local de demonstração de pelo menos 12 caracteres.
read -rsp "Senha de demonstração: " EVOOLUI_DEMO_PASSWORD
export EVOOLUI_DEMO_PASSWORD
python3 app/server.py seed
python3 app/server.py serve --host 127.0.0.1 --port 8000
```

Abra o servidor na porta 8000 usando o mecanismo de acesso disponibilizado pelo seu ambiente. O comando `seed` recusa sobrescrever um banco que já possui usuários. Todos os dados são fictícios e a interface mostra um aviso permanente no ambiente de demonstração.

Contas de teste (todas com a senha escolhida localmente na variável `EVOOLUI_DEMO_PASSWORD`; nenhuma senha é salva no código):

| E-mail | Perfil |
| --- | --- |
| `admin@evoolui.test` | Administrador |
| `socia@evoolui.test` | Sócio |
| `bia@evoolui.test` | Funcionária, clientes Aurora e Botânica |
| `lucas@evoolui.test` | Funcionário, cliente Forma |
| `cliente@evoolui.test` | Cliente, apenas Aurora |

A demonstração e o uso real devem utilizar bancos separados. Para iniciar um banco vazio real, configure variáveis por mecanismo seguro (não use os valores de exemplo como credenciais reais):

```bash
export EVOOLUI_DB=/caminho/persistente/evoolui.db
# Configure EVOOLUI_ADMIN_EMAIL e EVOOLUI_ADMIN_PASSWORD com uma senha exclusiva de 12+ caracteres.
python3 app/server.py bootstrap
python3 app/server.py serve
```

`bootstrap` cria o primeiro administrador e recusa um banco já inicializado. A partir daí, o administrador cadastra a equipe e os acessos de clientes em **Equipe e acessos**. Ao criar um acesso de cliente, vincule sua empresa. Cadastre a empresa antes e atribua os funcionários responsáveis no formulário de cliente.

## Base preservada — etapa 1

- Login e logout reais, sessões persistidas com expiração de 8 horas, senhas com PBKDF2-SHA256 e salt individual, cookies HttpOnly/SameSite e proteção CSRF.
- Perfis aplicados no servidor: administrador controla acessos; sócio consulta a equipe e gerencia a operação; funcionários acessam clientes atribuídos e suas próprias atividades; clientes consultam apenas sua empresa e as respectivas atividades.
- Clientes: cadastro, edição, consulta, busca, contato, telefone, Instagram, serviços, contrato em centavos, vencimento, situação e atribuição de funcionários. Valores financeiros somente para administrador e sócios.
- Equipe/acessos: cadastro, edição, busca, perfis, vínculo ao cliente, alteração de senha e desativação. Alterações de senha, permissão, empresa ou desativação revogam sessões existentes. Proteção contra remoção do próprio acesso administrativo.
- Agenda: calendário mensal, navegação entre meses, consulta, cadastro, edição, reagendamento e cancelamento manual; tipo de compromisso, responsável, cliente e observações. Impede sobreposição do responsável, inclusive em escritas concorrentes.
- Tarefas: cadastro, edição, consulta, busca e filtro por situação, responsável, cliente, prazo, prioridade, observações e conclusão.
- Dashboard com indicadores calculados dos registros acessíveis, agenda diária e alertas de prazo. Receita contratada é a soma dos contratos financeiros ativos e vigentes, **não faturamento recebido**. Não há números fictícios fixados no painel.
- Auditoria de login, criação e edição, acessível ao administrador. Os dados da demonstração são sempre identificados como fictícios.
- Dados persistem no SQLite entre reinicializações. Horários dos compromissos e prazos são horários de parede da agência em `America/Sao_Paulo`, sem conversão para o fuso do visitante. Alertas e indicadores atualizam ao carregar/recarregar dados; o painel atualiza ao carregar dados. As notificações internas dos novos módulos também são processadas em segundo plano com o servidor ativo.

As operações de exclusão definitiva não estão expostas: desative clientes/acessos, cancele compromissos ou conclua tarefas para preservar o histórico.

## Verificação

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js  # opcional: requer Node.js
```

A suíte de integração cria um banco temporário e sobe um servidor HTTP real em uma porta local livre. Cobre autenticação, CSRF, isolamento entre empresas, proteção financeira, atribuições, cadastros/edições persistentes, auditoria, tarefas, revogação, rollback de dados inválidos e conflito de agenda concorrente. Não modifica o banco da aplicação.

Teste opcional de navegador (requer o pacote Playwright e Chromium, não são dependências da aplicação):

```bash
# Use um banco separado: o teste adiciona registros fictícios.
EVOOLUI_DB=/tmp/evoolui-ui-test.db python3 app/server.py seed
EVOOLUI_DB=/tmp/evoolui-ui-test.db python3 app/server.py serve --port 8001
# Em outro terminal, defina e exporte a mesma EVOOLUI_DEMO_PASSWORD:
EVOOLUI_TEST_URL=http://127.0.0.1:8001 EVOOLUI_CHROMIUM=/usr/bin/chromium node tests/browser.cjs
```

As capturas opcionais ficam em `/tmp/evoolui-desktop.png` e `/tmp/evoolui-mobile.png`. O teste verifica login, cadastro de cliente, persistência após recarregar, criação de agenda/tarefa, calendário e largura móvel.

## Segurança, dados e publicação

Esta entrega é a base funcional da primeira etapa para desenvolvimento e avaliação. O servidor HTTP da biblioteca padrão **não é um servidor de produção**. Para publicar com dados reais, é necessário preparar hospedagem com HTTPS, servidor/gateway apropriado, backups automatizados, monitoramento, limites de requisições no proxy e revisão de segurança. Não exponha diretamente a porta de desenvolvimento na internet.

- Em HTTPS, defina `EVOOLUI_SECURE_COOKIE=1` para cookies Secure. Não desative verificações TLS.
- `EVOOLUI_DB` deve apontar para volume persistente com acesso restrito. O arquivo do banco é criado com permissão `0600`; proteja também diretório, backups e snapshots. Banco não criptografado em repouso pela aplicação.
- Não há senhas ou tokens em respostas da API. Sessões armazenam o hash do token. Consultas usam parâmetros e a interface escapa texto de usuários. Não há CORS aberto.
- Limitação de 15 falhas de login por IP em 15 minutos é local ao processo. Para produção, use rate limiting compartilhado e configuração correta do proxy; a aplicação não confia em cabeçalhos de IP enviados pelo cliente.
- Backups consistentes podem ser feitos com a API de backup do SQLite; teste restauração antes de operar com dados reais. Não copie banco em uso sem uma estratégia consistente.
- Para LGPD, ainda é preciso definir base legal, política de privacidade, retenção, procedimentos de exportação/exclusão e resposta a incidentes. Esta implementação não representa certificação de conformidade. Auditoria guarda IDs e ações, não os corpos completos das requisições.
- Recuperação de senha por e-mail, MFA e gestão avançada de sessões ainda não estão implementados. Arquivos privados e versões estão disponíveis conforme a seção abaixo. O administrador pode redefinir senhas pelo formulário de acesso. Não há opção pública de autorregistro.

## Arquivos privados e aprovações — disponíveis

Acesse **Arquivos**, escolha a empresa e clique em **Enviar arquivos**. O atalho **Arquivos** na lista de clientes abre diretamente a pasta da empresa. É possível enviar múltiplos arquivos com progresso individual; se um envio falhar, a interface informa quantos já foram gravados. Atualize a pasta antes de repetir um envio cuja conexão foi interrompida.

- Administradores/sócios acessam todas as empresas; funcionários acessam e enviam somente para empresas às quais estão atribuídos; clientes acessam apenas sua própria empresa e não podem enviar arquivos.
- Fotos e artes: PNG, JPG/JPEG, GIF e WebP; vídeos: MP4, MOV e WebM; documentos: PDF, TXT UTF-8, DOCX, XLSX e PPTX. SVG, HTML, executáveis, arquivos Office com macros e outros formatos são rejeitados. Documentos são entregues como download, sem execução no navegador. A reprodução de vídeo depende do codec suportado pelo navegador; não há transcodificação.
- Extensão e assinatura do conteúdo são verificadas. O tipo enviado pelo navegador não é considerado confiável. Isso não equivale a uma análise antimalware completa.
- Upload em streaming, com limite padrão de **250 MB por arquivo** (`EVOOLUI_MAX_UPLOAD_MB`) e **5 GB por empresa, incluindo todas as versões** (`EVOOLUI_CLIENT_STORAGE_MB`, padrão 5120). O proxy de hospedagem também deve aceitar esse tamanho e tempo de transferência. Falta de espaço em disco continua sendo uma limitação de infraestrutura.
- Arquivos ficam por padrão em `data/private_files/<id-da-empresa>/`, fora de `static/`. Nomes físicos aleatórios, diretórios `0700` e arquivos `0600`. `EVOOLUI_FILES_DIR` pode apontar para outro diretório/volume privado persistente. Não publique esse diretório por servidor estático, CDN ou bucket público.
- Cada consulta, reprodução, download, envio e avaliação verifica a sessão e a permissão atual no servidor. Remover uma atribuição impede os próximos acessos, inclusive a URLs já conhecidas. Nenhum link público ou chave de armazenamento é retornado ao navegador. Cópias já baixadas não podem ser revogadas.
- Vídeos usam respostas HTTP Range autenticadas para reprodução e busca temporal. Downloads preservam o nome original via cabeçalhos seguros. Não há cache público.
- Cada upload cria uma tarefa de acompanhamento com prazo de três dias, ou pode vincular uma tarefa de conteúdo existente da mesma empresa (envio único). Uma tarefa de gravação não pode ser reutilizada como tarefa de aprovação. Um conteúdo pode ter várias versões e uma única tarefa de acompanhamento.
- Ao receber o arquivo, o cliente ganha uma notificação interna. Somente o cliente da empresa pode aprovar ou solicitar alterações. Ajustes exigem uma observação: a tarefa fica **Em andamento**; aprovação a marca como **Concluída** e notifica os responsáveis.
- **Enviar nova versão** preserva os bytes, comentários e aprovação anteriores, deixa a versão nova **Aguardando aprovação** e reabre a mesma tarefa. Não é possível avaliar uma versão substituída, nem sobrescrever uma avaliação já registrada. A pasta mostra as versões atuais; o detalhe mantém todo o histórico.
- Logs registram envios, visualizações, downloads e avaliações. Não existe exclusão física pela interface nesta etapa; defina a retenção e a rotina de descarte antes de utilizar com dados pessoais reais.

A inicialização cria tabelas/colunas adicionais sem apagar os cadastros existentes. A migração é repetível. Faça backup consistente do banco **e dos arquivos**; um backup só de SQLite não contém os vídeos e documentos. Arquivos temporários de uma transferência interrompida por desligamento abrupto podem exigir limpeza operacional; eles não são expostos pela API.

## Agendamentos e tarefas automáticas — disponíveis

O cliente usa **Agendamentos → Solicitar gravação** e escolhe um horário de um funcionário atribuído à sua empresa. Nesta etapa os slots são de uma hora, de segunda a sexta, das 9h às 18h, nos próximos 90 dias, em `America/Sao_Paulo`. Não há configuração de jornada individual, feriados ou integração com calendários externos.

Solicitações pendentes **não reservam** o horário. A equipe responsável ou a gestão confirma ou recusa a solicitação. A confirmação verifica conflitos novamente em transação: duas aprovações concorrentes para o mesmo funcionário não geram sobreposição. Confirmar novamente a mesma solicitação não duplica agenda, tarefa nem notificação.

Uma confirmação cria o compromisso, uma tarefa atribuída ao responsável com prazo no término da gravação e notificações internas para cliente/equipe. Cliente ou equipe autorizada pode cancelar; a agenda é cancelada e a tarefa automática ganha situação **Cancelada**, distinta de uma entrega realizada. Reagendamento pela equipe na agenda mantém solicitação, prazo e responsável sincronizados e gera novo aviso. O cliente pode cancelar e solicitar outro horário.

## Automações e notificações internas — disponíveis

O servidor executa um ciclo de automação ao iniciar e a cada 60 segundos. Consultar a central de notificações também processa pendências de modo idempotente. As notificações são persistidas por destinatário, têm controle de leitura e respeitam a empresa/perfil atual do usuário. A interface atualiza o contador a cada 30 segundos; a central mostra até as 200 notificações mais recentes acessíveis.

- Solicitação, confirmação, reagendamento e cancelamento de gravações.
- Lembrete quando a gravação confirmada entra na janela das próximas 24 horas (uma vez por horário).
- Aviso ao responsável por nova tarefa ou edição, prazo nas próximas 24 horas e atraso. Atrasos também notificam a gestão.
- Novo conteúdo e nova versão para o cliente; aprovação ou ajustes para equipe e gestão. Pendências de aprovação recebem lembrete diário a partir do dia seguinte.
- Vencimentos financeiros nos próximos três dias ou atrasados; término de contrato nos próximos sete dias, somente para a gestão.

Os avisos são **internos à plataforma**, não e-mail, push do sistema operacional ou WhatsApp. O servidor precisa permanecer ativo para o processamento periódico; após reiniciar, o ciclo identifica pendências atuais. Não há garantia de entrega em um horário exato ou reconstituição de lembretes cuja janela já passou durante uma indisponibilidade. Em produção, supervisione o serviço/worker.

## Financeiro — disponível para administrador e sócios

A área **Financeiro** inclui contratos, receitas, despesas, vencimentos, pagamentos registrados manualmente, saldos e relatório mensal com exportação CSV. Valores são armazenados em centavos; pagamentos com data futura são rejeitados.

Os contratos mensais que já existiam no cadastro de clientes são importados uma única vez, preservando seus valores. **A partir desta etapa, gerencie contratos em Financeiro**: o campo original no cadastro de cliente é legado e não sincroniza mudanças em contratos já importados. O dashboard usa os contratos financeiros ativos/vigentes.

Contratos ativos geram uma mensalidade a receber por contrato/mês, sem duplicação nas execuções seguintes. Um vencimento no dia 31 é ajustado ao último dia dos meses mais curtos. A geração usa o mês atual e a vigência do contrato; não cria cobranças históricas retroativas, nem faz rateio proporcional. Alterar/encerrar um contrato não apaga lançamentos já emitidos; eles podem ser ajustados ou cancelados explicitamente no financeiro. Mantenha os registros para auditoria.

O resumo **Recebido / Despesas pagas / Saldo realizado** usa a data do pagamento. **A receber** usa o vencimento do mês. O relatório lista itens com vencimento ou pagamento no mês selecionado e distingue valores pendentes de valores efetivamente pagos. Não há boleto, Pix, conciliação bancária, emissão fiscal, gateway de pagamento ou escrituração contábil.

## Assistente e serviços externos

**Assistente** oferece um resumo determinístico dos registros acessíveis, contagem de atrasos e prioridades ordenadas pelos prazos. A interface informa explicitamente que isso **não usa IA**. Roteiros e resumos gerados por modelo ainda não estão implementados.

A área **Integrações** informa o estado não conectado de e-mail, WhatsApp Business, Instagram, Meta Ads e IA. Requisitos, possíveis custos e próximos passos estão em [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md). Nenhuma chamada externa ou envio real é simulado. As automações internas não precisam de credenciais de terceiros; hospedagem, espaço em disco e operação contínua podem ter custos.

## Testes dos novos módulos

A suíte completa continua usando banco e armazenamento temporários separados do ambiente:

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/workspace.js
```

Além da regressão da primeira etapa, cobre isolamento em todos os endpoints de arquivos, CSRF, formatos inválidos, quotas, permissões de disco, Range/HEAD, versões concorrentes e aprovações, tarefas/notificações, retirada de acesso, agenda concorrente, idempotência, cancelamento/reagendamento e financeiro restrito com geração de mensalidades e relatórios.

Teste opcional completo no navegador (Playwright, Chromium e FFmpeg necessários **só para o teste**):

```bash
# Use um banco separado; o teste cria arquivos, tarefas, solicitações e despesas fictícias.
EVOOLUI_DB=/tmp/evoolui-feature-test.db python3 app/server.py seed
EVOOLUI_DB=/tmp/evoolui-feature-test.db python3 app/server.py serve --port 8001
# Em outro terminal, defina e exporte a mesma EVOOLUI_DEMO_PASSWORD:
ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=0x17674f:s=320x180:d=2 -c:v libx264 -pix_fmt yuv420p -movflags +faststart /tmp/evoolui-test-video.mp4
EVOOLUI_TEST_URL=http://127.0.0.1:8001 EVOOLUI_CHROMIUM=/usr/bin/chromium node tests/features-browser.cjs
```

Esse teste valida reprodução de vídeo real, download, ajustes/nova versão/aprovação, persistência, isolamento, layout móvel, solicitação/confirmação com tarefa automática, financeiro/CSV e central de notificações. Capturas ficam em `/tmp/evoolui-files-desktop.png` e `/tmp/evoolui-files-mobile.png`.

## Limitações e próximos passos

Pipeline editorial completo, publicação em redes sociais, calendário individual configurável, relatórios avançados de produtividade, geração por IA e integrações externas continuam pendentes. A aplicação continua sendo um servidor de desenvolvimento: HTTPS, servidor de produção, monitoramento, backup dos arquivos/banco, recuperação de desastre e políticas LGPD precisam ser preparados antes de operar com dados reais. Alterações de desenvolvimento não publicam uma versão do ambiente nem implantam a aplicação.
