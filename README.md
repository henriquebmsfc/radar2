# Radar 2.0

Acha canais do YouTube em inglês com até 21 dias de vida que já estão estourando, com vídeos longos narrados (documentário, história, ciência, "e se", geografia, economia, crime real, engenharia).

Cada rodada leva uns 20 minutos e mostra o resultado numa tela com capa, views e inscritos de cada canal. Depois, o Malandro mostra em quais línguas aquele canal ainda não foi copiado.

## O que você precisa

- **Python 3** (versão 3.9 ou mais nova).
- **Claude Code** com login feito. Ele filtra os canais pelo assunto, usando a sua assinatura do Claude.
- **Uma chave da API do YouTube**, que é grátis (passo a passo abaixo).

## Instalar

No terminal, dentro desta pasta:

```
python3 -m pip install -r requirements.txt
cp .env.exemplo .env
```

## Pegar a chave do YouTube

1. Entre em https://console.cloud.google.com e crie um projeto.
2. Em "APIs e serviços" > "Biblioteca", ative a **YouTube Data API v3**.
3. Em "APIs e serviços" > "Credenciais", crie uma **chave de API**.
4. Abra o arquivo `.env` e cole a chave depois de `YOUTUBE_API_KEY=`.

A chave é só sua: não mande para ninguém. O arquivo `.env` não é enviado para o Git.

## Rodar

```
python3 scripts/radar2/radar.py
```

Uma rodada gasta cerca de 7.300 dos 10.000 pontos que o YouTube dá por dia, então rode **uma vez por dia**. A cota volta à meia-noite no horário do Pacífico (de manhã cedo no Brasil).

Opções:

- `--api N`: quantas palavras pesquisar pela API (padrão 35; cada uma gasta 200 pontos).
- `--sem-gratis`: não usa a busca grátis.
- `--max-paginas N`: quantas páginas de vídeo ler atrás de canais parecidos (padrão 60).

## Ver o resultado

```
cd estudos/radar2/tela
python3 -m http.server 8000
```

Depois abra http://localhost:8000 no navegador. O que você salvar ou descartar fica guardado nesse navegador.

Os dados ficam em `estudos/radar2/`. O banco `radar2.sqlite` guarda o histórico; apagar ele faz o radar começar do zero.

## Malandro: ver em quais línguas o canal ainda não foi copiado

O radar acha o canal. O Malandro diz, língua por língua, se já existe alguém fazendo a mesma coisa.

1. Na tela, clique em **Salvar** no canal que interessou.
2. Abra a aba **Salvos**. O canal mostra o comando pronto, com o ID dele (começa com `UC`). Rode no terminal, a partir da pasta principal do projeto:

```
python3 scripts/radar2/malandro_salvos.py UCxxxxxxxxxxxx
```

3. Atualize a tela. O canal passa a mostrar cada língua como **livre**, **livre, pouco público** ou **já copiado**.

- **Análise 1 (padrão):** alemão, espanhol, francês, português, italiano, holandês, sueco e polonês.
- **Análise 2:** norueguês, dinamarquês, finlandês, romeno, croata e tcheco. Use `--analise 2`.

Cada análise leva alguns minutos por canal e usa a mesma chave do YouTube e o Claude Code. Se já houver análise com menos de 7 dias, ela é pulada; use `--refazer` para repetir.

**Se você já tem canais:** crie o arquivo `estudos/cacador/nossos_canais.json` com a lista dos IDs, por exemplo `["UCaaa", "UCbbb"]`. Assim eles não contam como cópia.

O resultado é uma pista, não uma certeza: confira a língua à mão antes de criar um canal.

O arquivo `scripts/cacador.py` é só apoio do Malandro; não precisa rodar ele.

## Como ele funciona

1. **Busca:** pesquisa palavras amplas de título ("why", "secret", "abandoned"...) em vídeos da última semana.
2. **Idade:** descobre a data de criação de cada canal e fica só com os de até 21 dias.
3. **Ficha:** lê vídeos longos, views e inscritos.
4. **Foco:** tira o que está fora do assunto por regras e pelo Claude (drama, notícia, famosos, vlog, games...).
5. **Bola de neve:** olha os recomendados ao lado dos vídeos que estouraram, para achar canais parecidos.
6. **Tela:** gera os dados que a página mostra.

As listas de palavras e de assuntos fora do foco ficam no começo de `scripts/radar2/radar.py` e podem ser mudadas à vontade.
