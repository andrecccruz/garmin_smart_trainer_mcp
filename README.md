# treinos-mcp

MCP local que deixa o Claude Code publicar treinos no **Intervals.icu**, que sincroniza com o
**Garmin Connect** dos atletas. Você conversa ("monta a semana da atleta1"), aprova, e o treino
chega no relógio.

```
Claude Code ──stdio──▶ intervals-mcp ──HTTPS──▶ Intervals.icu ──▶ Garmin Connect ──▶ relógio
     │
     └── lê o contexto dos atletas numa pasta local, fora deste repo
```

## O que o MCP pode fazer

| Ferramenta | O que faz | Permissão no Claude Code |
|---|---|---|
| `listar_atletas` | apelidos autorizados | livre |
| `listar_treinos` | treinos e provas de um período (até 90 dias) | livre |
| `atividades_recentes` | resumo do que foi feito (até 42 dias): duração, FC, carga, zonas, RPE | livre |
| `criar_treino` | publica um treino (hoje até +120 dias) | **pede confirmação** |
| `atualizar_treino` | altera um treino criado pelo MCP | **pede confirmação** |
| `apagar_treino` | apaga um treino futuro criado pelo MCP | **pede confirmação** |

Travas no código:
- URL da API fixa (`https://intervals.icu`), sem seguir redirecionamento com a chave.
- Só atletas listados em `INTERVALS_ATHLETES`.
- Alterar/apagar só vale para treinos que o próprio MCP criou (`external_id` com prefixo `tmcp-`).
  Treinos criados pela atleta ou por outro app ficam intocáveis.
- Não lê bem-estar (sono, HRV, peso), streams ou GPS. Notas do calendário não são listadas.

## Setup (uma vez por máquina)

Pré-requisitos: Claude Code e [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
No openSUSE o Python 3 já vem instalado; o `uv` cuida do resto.

```bash
git clone git@github.com:SEU_USUARIO/treinos-mcp.git
cd garmin_smart_trainer_mcp
uv sync

cp .env.example .env && chmod 600 .env
# edite .env: INTERVALS_API_KEY e INTERVALS_ATHLETES

cp .claude/settings.local.example.json .claude/settings.local.json
# edite o caminho da pasta de contexto

mkdir -p ~/treinos-contexto
cp -r contexto-exemplo/atleta1 ~/treinos-contexto/<apelido>   # e preencha
```

Teste sem rede: `uv run pytest`.

Primeiro uso real: deixe `INTERVALS_DRY_RUN=1`, abra `claude` na pasta, aprove o MCP `intervals`
quando ele perguntar e peça para listar e simular um treino. Depois mude para `0` e publique um
treino curto no seu próprio calendário para conferir que chega no Garmin.

## Uso no dia a dia

```bash
cd garmin_smart_trainer_mcp && claude
> monta a próxima semana da atleta1
```

O fluxo completo (ler contexto → ver calendário e atividades → propor → aprovar → publicar →
registrar no `plano.md`) está no `CLAUDE.md`.

## Lado da atleta (uma vez)

1. Conta no Intervals.icu com o Garmin conectado em **Settings → Connections**, marcando
   **Baixar atividades** e **Carregar treinos planejados**. As atividades precisam vir do Garmin:
   as importadas do Strava não ficam acessíveis pela API.
2. Aceitar você como treinador no Intervals. Com isso a **sua** API key passa a funcionar para o
   athlete ID dela. Ela não precisa te passar nenhuma credencial e pode revogar quando quiser.
3. Te passar o athlete ID (começa com `i`, está na URL do perfil).

## Rodar isolado com Podman (opcional)

```bash
podman build -t intervals-mcp:latest -f Containerfile .
```

Troque o `.mcp.json` por:

```json
{
  "mcpServers": {
    "intervals": {
      "command": "podman",
      "args": [
        "run", "-i", "--rm",
        "--read-only", "--tmpfs", "/tmp",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--memory=256m", "--pids-limit=64",
        "--env-file", "${CLAUDE_PROJECT_DIR:-.}/.env",
        "localhost/intervals-mcp:latest"
      ]
    }
  }
}
```

Rootless, sem daemon, filesystem só leitura, sem capabilities. O container nasce quando a sessão
do Claude Code abre e morre quando fecha. Depois de mudar o código, rode o `podman build` de novo.

## Segurança

- `.env` e `.claude/settings.local.json` ficam fora do Git (`.gitignore`). O
  `.claude/settings.json` também nega ao Claude ler o `.env`.
- Hook de pre-commit com `gitleaks` + bloqueio de `.env` e pastas de atleta:
  `uvx pre-commit install`.
- Se a chave vazar: Intervals → Settings → Developer → gerar nova, e atualizar o `.env`.
  Commit com chave é vazamento mesmo depois de apagado; o histórico do Git é permanente.
- A API key do Intervals não tem escopo granular: trate como senha.
