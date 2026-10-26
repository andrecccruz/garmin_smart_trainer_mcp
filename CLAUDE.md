# Treinos MCP — instruções para o Claude

Este repositório contém um MCP (`intervals`) que publica treinos no Intervals.icu. O Intervals
sincroniza com o Garmin Connect de cada atleta e o treino aparece no relógio.
O usuário é o treinador. Os atletas aparecem sempre por **apelido**.

## Onde está o contexto dos atletas

O contexto **não fica neste repo**. Ele está numa pasta local configurada em
`.claude/settings.local.json` (`permissions.additionalDirectories`). Leia esse arquivo para
descobrir o caminho. Estrutura esperada:

```
<pasta-de-contexto>/<apelido>/perfil.md   # objetivo, zonas, disponibilidade, restrições
<pasta-de-contexto>/<apelido>/plano.md    # bloco atual, semanas publicadas, observações
```

Se a pasta ou o atleta não existir, avise e ofereça criar a partir de `contexto-exemplo/`.

Regras sobre esses dados:
- Nunca copie nada do contexto para dentro deste repositório (código, commits, CLAUDE.md, testes).
- Nunca leia `.env`. A chave é usada só pelo processo do MCP.
- Não faça diagnóstico. Restrições do perfil são regras de treino a respeitar, não algo a interpretar.

## Fluxo para "monta/atualiza a semana do <apelido>"

1. Ler `perfil.md` e `plano.md` do atleta.
2. `listar_treinos` do período, para não duplicar e ver provas (`RACE_*`).
3. `atividades_recentes` (14 dias costuma bastar) para comparar o planejado com o feito.
   Resuma em 2–3 linhas: aderência, carga, algo fora do normal.
4. Propor a semana numa tabela: dia | esporte | nome | duração | alvo | objetivo do treino.
   Mostrar também o texto de cada treino na sintaxe do Intervals.
5. **Esperar aprovação explícita.** Ajustes viram nova proposta.
6. Publicar com `criar_treino`, um por treino. Se a resposta trouxer `aviso`, a sintaxe falhou:
   corrigir com `atualizar_treino` antes de seguir.
7. Atualizar `plano.md` do atleta: semana publicada (datas, nomes, ids) e decisões tomadas.

Alterar ou apagar: só treinos com `criado_pelo_mcp: true`. Os outros o treinador ajusta no Intervals.
Apagar exige confirmação explícita por treino.

## Critérios de treino

- Sem medidor de potência: alvo por **FC** (bike) ou **FC/pace** (corrida). Só use % de FTP se o
  `perfil.md` disser que o atleta tem medidor e FTP testado.
- Se o perfil não tiver FC de limiar (LTHR), proponha primeiro o teste de limiar
  (30 min contínuos, LTHR = FC média dos últimos 20 min) em vez de chutar zonas.
- Progressão gradual de volume; semana mais leve a cada 3–4 semanas; respeite a disponibilidade
  declarada. Na dúvida, prescreva menos e explique o porquê.

## Sintaxe de treino do Intervals.icu (campo `texto`)

Um passo por linha, começando com `-`. Duração (`30s`, `10m`, `1h`, `1m30s`) ou distância
(`400mtr`, `2km`), seguida do alvo. Linha `Nx` abre um bloco de repetição até a próxima linha
em branco. Linhas sem `-` são títulos de seção.

```
Aquecimento
- 15m Z1-Z2 HR

Principal 4x
- 6m Z4 HR
- 3m Z1 HR

Desaquecimento
- 10m Z1 HR
```

Alvos comuns:
- `Z2 HR` (zona de FC), `Z1-Z2 HR` (faixa de zonas)
- `85-90% LTHR` (% da FC de limiar), `70-75% HR` (% da FC máxima)
- `Z3 Pace`, `90-95% Pace` (corrida, relativo ao pace de limiar)
- `75%` sozinho = % de FTP (evitar sem medidor de potência)

Depois de publicar, `duracao_min` preenchido indica que o Intervals entendeu os passos.

## Esportes aceitos

`Ride`, `VirtualRide`, `GravelRide`, `MountainBikeRide`, `Run`, `VirtualRun`, `TrailRun`, `Swim`,
`OpenWaterSwim`, `Walk`, `Hike`, `WeightTraining`, `Workout`.
