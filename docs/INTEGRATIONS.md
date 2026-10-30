# Integrações externas — planejamento, sem conexões ativas

As automações entregues nesta etapa rodam localmente e persistem notificações internas no SQLite. **Não há envio de e-mail, WhatsApp, publicação em redes sociais, cobrança bancária ou chamada a modelos de IA.** A área Integrações da interface informa esses estados.

| Integração futura | Configuração e autorização necessárias | Custos potenciais |
| --- | --- | --- |
| E-mail transacional | Provedor SMTP/API, domínio remetente verificado, SPF/DKIM/DMARC e credencial segura. Implementar entrega, tentativas, bounce e unsubscribe quando aplicável. | Plano do provedor e volume de envios. |
| WhatsApp Business | API oficial da Meta, conta Business, número habilitado, token com escopo apropriado, consentimento e templates aprovados. Implementar webhooks assinados e opt-out. | Tarifas e plano do provedor, conforme país, categoria e regras vigentes. |
| Instagram / Meta Ads | Aplicativo Meta, OAuth por conta, permissões aprovadas, revisão quando necessária e contas profissionais/anúncios vinculadas. Implementar renovação/revogação de tokens e limites de API. | Eventual infraestrutura; mídia paga continua sendo um custo separado. |
| Assistente de IA | Escolha de provedor/modelo, credencial segura, orçamento, limites por usuário e política de dados. Implementar chamadas reais, tratamento de erros e registro de uso. | Uso de API por modelo/volume. |
| Armazenamento privado em nuvem | Bucket privado, identidade de serviço com menor privilégio, criptografia, backup e política de retenção. Implementar adaptador mantendo a verificação de empresa antes de cada acesso. | Espaço, operações e tráfego de saída. |

Não são necessárias credenciais externas para os recursos internos atuais. Não insira credenciais em comentários, arquivos versionados ou campos de empresa. Configure-as em um gerenciador de segredos quando a integração correspondente for implementada.

## Próximos passos técnicos

- Separar o agendador em um worker supervisionado para operação contínua e acrescentar uma fila de entrega externa com idempotência, retentativas limitadas e registro de resultado. Hoje a tabela `notifications` representa notificações internas; não confirma entrega a serviços externos.
- Implementar adaptadores de provedores que recebam apenas destinatários/empresas autorizados e dados necessários. Registrar estados como `não configurado`, `configurado`, `entregue` e `falhou` com evidências reais.
- Antes de enviar conteúdo à IA, aplicar os mesmos filtros de permissão do backend, definir consentimento/base legal, retenção e política do provedor. Sugestões nunca devem alterar tarefas, finanças ou aprovações sem fluxo explícito de confirmação.
- Adicionar processamento antimalware/quarentena e transcodificação se houver upload público/alto volume. A validação atual de extensão e assinatura não equivale a antivírus.

Não há credenciais solicitadas ou valores de custo fixados nesta entrega. Preços e permissões devem ser consultados nas fontes oficiais do provedor escolhido na implementação.
