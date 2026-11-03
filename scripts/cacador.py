# Caçador de canais novos (radar antigo; aqui serve só de apoio para o Malandro): acha canais com até 6 meses que já estão crescendo.
# Uso: python3 scripts/cacador.py                 (todas as línguas e sementes)
#      python3 scripts/cacador.py --linguas pl,ro --max-sementes 3   (teste rápido)
#
# Como funciona:
#   1. BUSCA: para cada semente (estudos/cacador/sementes.json) em cada língua, pesquisa no YouTube os vídeos
#      mais vistos do MÊS e da SEMANA. Guarda os canais que aparecem com vídeo acima de MIN_VIEWS.
#   2. TRIAGEM: lê o feed (RSS) de cada canal ainda não visto. O feed traz a data de criação do canal e os
#      últimos 15 vídeos com título ORIGINAL (a busca traduz os títulos). Canal com mais de 6 meses vira
#      "velho" e nunca mais é checado.
#   3. FICHA: para os canais novos, lê a lista completa de vídeos e Shorts (views, duração) e os inscritos,
#      descobre a língua pelos títulos e grava uma "foto" do dia (para medir o crescimento entre um dia e outro).
#   4. Exporta estudos/cacador/canais.json (o que o painel mostra).
import argparse, concurrent.futures as cf, datetime as dt, json, re, sqlite3, statistics as st, subprocess, sys, time
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "estudos" / "cacador"
BANCO = PASTA / "cacador.sqlite"
LINGUAS = ["en", "pt", "es", "fr", "de", "it", "ro", "hr", "pl"]
MAX_DIAS = 21           # "novo" = criado (ou começou a postar) há no máximo 3 semanas (usuário: 14 em 23/10, 21 em 28/10/2026; 30 = já copiaram)
MIN_VIEWS = 2000        # vídeo da busca abaixo disso não vale checar o canal
FILTROS = {"mes": "CAMSBAgEEAE%3D", "semana": "CAMSBAgDEAE%3D"}   # mais vistos + só vídeos + período
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) radar/0.1"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}
HOJE = dt.datetime.now(dt.timezone.utc)


def baixar(url):
    for tentativa in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if tentativa == 3:
                raise
        except Exception:
            if tentativa == 3:
                return None
        time.sleep(2 + tentativa * 4)


def ytdlp(url, fim):
    # lang=en: com outras línguas o yt-dlp às vezes lê "8 mln" como 8 views
    # None = falhou mesmo depois de tentar de novo (diferente de "não achou nada")
    for tentativa in range(3):
        try:
            o = subprocess.run(["yt-dlp", "--js-runtimes", "node", "--extractor-args", "youtube:lang=en", "--flat-playlist",
                                "-J", "--playlist-end", str(fim), url], capture_output=True, text=True, timeout=300)
            if o.returncode == 0 and o.stdout:
                return json.loads(o.stdout)
            if "does not have" in o.stderr or "not available" in o.stderr or "does not exist" in o.stderr:
                return {}   # canal sem aba de Shorts/vídeos: resposta normal, não é falha

        except Exception:
            pass
        time.sleep(4 + tentativa * 6)
    return None


GOOGLE_BLOQUEADO = {"ate": 0}   # depois de um bloqueio, vai direto para o tradutor reserva por 30 min


def traduzir(texto, para):
    # pelo curl: o tradutor devolve 429 para o urllib do Python
    u = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&dt=t&tl=" + para
    for tentativa in range(0 if time.time() < GOOGLE_BLOQUEADO["ate"] else 1):
        o = subprocess.run(["curl", "-s", "-A", "Mozilla/5.0", u, "--data-urlencode", "q@-"], input=texto,
                           capture_output=True, text=True, timeout=30)
        if o.stdout.startswith("[["):
            r = json.loads(o.stdout)
            return "".join(p[0] for p in r[0]), r[2]
        GOOGLE_BLOQUEADO["ate"] = time.time() + 1800
    # reserva quando o Google bloqueia: MyMemory (grátis, ~5.000 caracteres/dia sem cadastro)
    de = lingua_de([texto]) or "en"
    if de == para:
        return texto, de
    u2 = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": texto[:500], "langpair": f"{de}|{para}"})
    r = json.loads(baixar(u2) or "{}")
    t = (r.get("responseData") or {}).get("translatedText")
    if not t or r.get("quotaFinished"):
        raise RuntimeError("tradutores indisponíveis agora; tente mais tarde")
    return t, de


def lingua_de(titulos):
    # detector local (o tradutor do Google bloqueia com muitos pedidos); croata, bósnio e sérvio se confundem
    if not titulos:
        return None
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0
    try:
        l = detect(" . ".join(t for t in titulos[:15] if t))
    except Exception:
        return None
    return {"sh": "hr", "bs": "hr", "sr": "hr"}.get(l, l)


def data(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def abrir_banco():
    PASTA.mkdir(parents=True, exist_ok=True)
    b = sqlite3.connect(BANCO)
    b.executescript("""
    create table if not exists canais(id text primary key, nome text, handle text, criado text, inicio text,
        status text, lingua text, achado_em text, achado_por text, ficha text, atualizado text);
    create table if not exists fotos(canal text, dia text, inscritos int, views int, videos int, shorts int,
        primary key(canal, dia));
    """)
    return b


def sementes():
    arq = PASTA / "sementes.json"
    s = json.loads(arq.read_text())
    faltando = [t for t in s["en"] if any(t not in s.get("_traducoes", {}).get(l, {}) for l in LINGUAS[1:])]
    if faltando:   # traduz uma vez e guarda; dá para corrigir à mão no arquivo depois
        tr = s.setdefault("_traducoes", {})
        for l in LINGUAS[1:]:   # uma chamada por língua, um tema por linha
            linhas = traduzir("\n".join(s["en"]), l)[0].split("\n")
            if len(linhas) != len(s["en"]):
                sys.exit(f"tradução para {l} veio com {len(linhas)} linhas, esperava {len(s['en'])}")
            tr[l] = {t: x.strip().lower() for t, x in zip(s["en"], linhas)}
            time.sleep(1)
        arq.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    return {l: [(t, t if l == "en" else s["_traducoes"][l][t]) for t in s["en"]] for l in LINGUAS}


def sementes_en():
    # pesquisas só em inglês, por subnicho (estudos/cacador/sementes_en.json): [(grupo, termo)]
    s = json.loads((PASTA / "sementes_en.json").read_text())
    return [(g, t) for g, termos in s.items() if not g.startswith("_") for t in termos]


def parecidos(max_titulos=6):
    # "Seus canais acharam": os títulos que mais deram views nos canais de referência viram pesquisas
    arq = PASTA / "referencias.json"
    refs = json.loads(arq.read_text()) if arq.exists() else []
    tarefas = []
    for f in fichas_api([r["id"] for r in refs], {}):
        for v in f["videos"][:max_titulos]:
            tarefas.append(("parecidos: " + f["nome"], v["titulo"]))
    return tarefas


# ---- buscas que se renovam (28/10/2026): o radar repetia as mesmas 306 pesquisas todo dia e achava sempre os mesmos vídeos
RENDE = PASTA / "rendimento.json"          # {termo: [vezes usado, canais novos achados]}
AUTO = PASTA / "sementes_auto.json"        # pesquisas criadas pela IA: {"data": ..., "termos": [[grupo, termo], ...]}
APOSENTA_USOS = 4                          # usado 4 vezes sem achar nenhum canal novo = aposentado


def config_painel():
    # tipos escondidos e nichos parados no painel
    try:
        return json.loads((PASTA / "config_painel.json").read_text())
    except Exception:
        return {}


# tipo escondido no painel (classificador) → grupo de pesquisa que só traz esse tipo também para de ser pesquisado
TIPO_GRUPO = {"Mitologia e religião": "mitologia, religião e bíblia", "Natureza e animais": "natureza e animais",
              "Carros e veículos": "carros e veículos", "Drama narrado (ficção)": "histórias narradas",
              "Aposentadoria e idosos": "dinheiro e aposentadoria", "Saúde e bem-estar": "saúde, idosos e bem-estar"}


def grupos_parados(cfg=None):
    cfg = cfg if cfg is not None else config_painel()
    return set(cfg.get("grupos_fora") or []) | {TIPO_GRUPO[t] for t in cfg.get("tipos_ocultos") or [] if t in TIPO_GRUPO}


def rendimento():
    try:
        return json.loads(RENDE.read_text())
    except Exception:
        return {}


def aposentado(termo, rend):
    usos, novos = rend.get(termo, [0, 0])
    return usos >= APOSENTA_USOS and novos == 0


def irmaos(n_canais=12, por_canal=2):
    # títulos dos canais do painel que estão estourando viram pesquisa: quem copia um canal que estourou costuma ser novo
    try:
        canais = json.loads((PASTA / "painel" / "dados.json").read_text())["canais"]
    except Exception:
        return []
    ocultos = set(config_painel().get("tipos_ocultos") or [])
    bons = sorted([c for c in canais if (c.get("medl") or 0) >= 10000 and c.get("tipo") not in ocultos
                   and c.get("prod") != "filmagem própria ou rosto"], key=lambda c: -(c.get("medl") or 0))[:n_canais]
    out = []
    for c in bons:
        for v in sorted(c["videos"], key=lambda v: -v["v"])[:por_canal]:
            out.append(("irmãos de " + c["nome"], v["t"]))
    return out


def termos_ia(n=100):
    # uma vez por dia a IA (Haiku) cria pesquisas NOVAS a partir do que está estourando, fora dos nichos escondidos/parados
    hoje = HOJE.date().isoformat()
    try:
        a = json.loads(AUTO.read_text())
    except Exception:
        a = {"data": None, "termos": []}
    if a.get("data") == hoje:
        return [tuple(x) for x in a["termos"]]
    from malandro import claude_cli
    cfg = config_painel()
    s = json.loads((PASTA / "sementes_en.json").read_text())
    grupos = [g for g in s if not g.startswith("_") and g not in grupos_parados(cfg)]
    ja = {t for g in s if not g.startswith("_") for t in s[g]} | {t for _, t in a.get("termos", [])}
    try:
        canais = json.loads((PASTA / "painel" / "dados.json").read_text())["canais"]
        top = [f"{c['videos'][0]['t']} ({c['medl']:,} views típicas)" for c in sorted(canais, key=lambda c: -(c.get("medl") or 0))
               if c.get("tipo") not in (cfg.get("tipos_ocultos") or [])][:25]
        import collections
        temos = collections.Counter(c.get("tipo") for c in canais if c.get("tipo") not in (cfg.get("tipos_ocultos") or []))
        temos = ", ".join(f"{t} ({n})" for t, n in temos.most_common())
    except Exception:
        top, temos = [], ""
    prompt = f"""Você ajuda um radar que procura canais NOVOS do YouTube em inglês que estão estourando (vídeos longos, narrados,
documentário/explicação). O radar pesquisa no YouTube os vídeos mais vistos do mês e da semana para cada pesquisa.
Grupos de assunto permitidos (use o NÚMERO): {"; ".join(f"{i}={g}" for i, g in enumerate(grupos))}.
NÃO queremos: {", ".join(cfg.get("tipos_ocultos") or [])}; nem survival, games, infantil, Índia/Sul da Ásia, Shorts.
Títulos que estão estourando agora em canais novos:
{chr(10).join("- " + t for t in top)}

Tipos de canal que o radar JÁ acha (com quantos canais): {temos}.

Crie {n} pesquisas NOVAS, curtas (2 a 6 palavras), em inglês, como um espectador digitaria, para achar canais DIFERENTES dos
que já temos: pelo menos metade em assuntos e formatos que aparecem pouco ou nada na lista acima (outros temas de
documentário narrado, outros jeitos de título, outras épocas, lugares, profissões, empresas, fenômenos); o resto variando
os assuntos que estouram. Os títulos que estouram servem de pista do estilo, não para repetir o assunto.
Não repita nenhuma destas: {", ".join(list(ja)[:150])}.
Responda SÓ com JSON: [{{"grupo": <número do grupo>, "termo": "..."}}, ...]"""
    try:
        o = subprocess.run([claude_cli(), "-p", prompt, "--model", "haiku", "--output-format", "text"],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        txt = o.stdout[o.stdout.find("["):o.stdout.rfind("]") + 1]
        novos = [(grupos[int(x["grupo"])], x["termo"].strip()) for x in json.loads(txt)
                 if x.get("termo") and x["termo"].strip() not in ja and str(x.get("grupo")).isdigit()
                 and int(x["grupo"]) < len(grupos)]
    except Exception as e:
        print("   pesquisas da IA falharam:", e, flush=True)
        novos = []
    rend = rendimento()
    # guarda as de dias anteriores que ainda não foram aposentadas (até 200)
    termos = (novos + [tuple(x) for x in a.get("termos", []) if not aposentado(x[1], rend)])[:400]
    AUTO.write_text(json.dumps({"data": hoje, "termos": termos}, ensure_ascii=False, indent=1))
    return termos


# ---- fórmulas de título (29/10/2026): o centro do radar. Teste: 20 pesquisas de fórmula acharam quase todos os canais
# que o usuário gostou (13% bons); ~900 pesquisas de assunto acharam 3% bons. Canal novo que estoura copia uma FÓRMULA de
# título ("The ENTIRE History of X in N Minutes", "Who Really Makes X for Y"). As fórmulas saem dos títulos que estouraram
# nas buscas de assunto do próprio dia (VIRAIS); a que traz canal bom fica, a que só traz lixo é aposentada.
FORMULAS = PASTA / "formulas.json"   # {"data": ..., "lista": [{"f": fórmula, "ex": título de exemplo}, ...]}
VIRAL = 150000                       # vídeo da busca com isso ou mais entra na vitrine
VIRAIS = []                          # (views, título, nicho) colhidos pela buscar() durante a rodada
GENERICAS = {"the story of", "the history of", "the end of", "the battle of", "the fall of", "the king of", "the truth about the",
             "the untold story of", "the complete story of", "the full story of", "the rise and fall of", "the secret history of"}
MAX_FORMULAS = 150                   # pesquisas de fórmula por rodada (cada uma roda nos 2 filtros: mês e semana)


def qualidade_formulas():
    # canais bons (mediana >= 20 mil e 2+ vídeos com 10 mil) que cada fórmula já trouxe, pelo painel
    try:
        canais = json.loads((PASTA / "painel" / "dados.json").read_text())["canais"]
    except Exception:
        return {}
    ocultos = set(config_painel().get("tipos_ocultos") or [])
    q = {}
    for c in canais:
        if c["nicho"].startswith("fórmula: ") and c.get("tipo") not in ocultos:
            q[c["nicho"][9:]] = q.get(c["nicho"][9:], 0) + (c["medl"] >= 20000 and c["hits"] >= 2)
    return q


def titulos_dos_bons():
    # títulos que estouraram nos canais bons do painel (mediana >= 20 mil, tipo visível): fórmula já provada em canal novo
    try:
        canais = json.loads((PASTA / "painel" / "dados.json").read_text())["canais"]
    except Exception:
        return []
    ocultos = set(config_painel().get("tipos_ocultos") or [])
    out = []
    for c in canais:
        if c.get("lingua") == "en" and c.get("tipo") not in ocultos and c.get("medl", 0) >= 20000:
            for v in c.get("videos") or []:
                if v.get("v", 0) >= max(100000, 2 * c["medl"]):
                    out.append((v["v"], v["t"], c["nicho"]))
    # + o vídeo longo mais visto (150 mil+) de cada canal novo em inglês já fichado no banco
    b = sqlite3.connect(BANCO)
    for ficha, tipo, por in b.execute("select ficha, tipo, achado_por from canais where ficha is not null and lingua='en'"):
        if tipo in ocultos:
            continue
        vs = [v for v in json.loads(ficha).get("videos") or [] if not v.get("short") and v.get("duracao", 0) >= 180]
        m = max(vs, key=lambda v: v.get("views", 0), default=None)
        if m and m["views"] >= 150000:
            out.append((m["views"], m["titulo"], (por or "").split(":", 1)[-1]))
    return out


def formulas_do_dia(n_novas=80, forcar=False, pular=0):
    # fórmulas que continuam + novas tiradas pela IA (Haiku) dos títulos que estouraram hoje
    try:
        a = json.loads(FORMULAS.read_text())
    except Exception:
        a = {"data": None, "lista": []}
    rend, bons = rendimento(), qualidade_formulas()
    # aposentada: 4 usos sem canal novo (regra geral) ou 6 usos sem nenhum canal bom
    vivas = [x for x in a["lista"] if not aposentado(x["f"], rend) and not (rend.get(x["f"], [0])[0] >= 6 and not bons.get(x["f"]))]
    hoje = HOJE.date().isoformat()
    fontes = VIRAIS + titulos_dos_bons()
    if (a.get("data") != hoje or forcar) and fontes:
        from malandro import claude_cli
        cfg = config_painel()
        vistos, titulos = set(), []
        for v, t, nicho in sorted(fontes, reverse=True):
            if t not in vistos and nicho not in grupos_parados(cfg):
                vistos.add(t)
                titulos.append(f"{t} ({v // 1000} mil views)")
        ja = [x["f"] for x in a["lista"]]
        prompt = f"""Você ajuda um radar que procura canais NOVOS do YouTube em inglês que estão copiando um formato que estourou.
Canal novo que estoura quase sempre copia uma FÓRMULA de título. Exemplos de fórmula (a parte fixa do título, sem o assunto):
"entire history of in minutes" (de "The ENTIRE History of the KGB in 13 Minutes"); "who really makes" e "the answer may
surprise you" (de "Who Really Makes Hercules Tools for Harbor Freight – The Answer May Surprise You"); "is hiding something
nobody talks about"; "places the mob used to control"; "the real reason never"; "wtf happened to".

Títulos que estouraram este mês:
{chr(10).join("- " + t for t in titulos[pular:pular + 400])}

Tire até {n_novas} fórmulas NOVAS desses títulos: 3 a 7 palavras em inglês, só as palavras fixas da estrutura (sem nomes,
lugares, números ou o assunto específico), do jeito que se digita na busca do YouTube. A fórmula precisa ter o GANCHO do
título (a promessa, a surpresa, o mistério: "the answer may surprise you", "nobody talks about", "shouldn't exist"); nada
genérico que serve para qualquer documentário ("the story of", "the history of", "the end of", "the battle of", "the fall of"). Só de formatos que dão para fazer
com narração + material real (fotos, arquivos, jornais, mapas, documentos, banco de imagens). NÃO: ficção/drama narrado,
filme completo, vlog em primeira pessoa, apresentador ou podcast, recortes de TV, notícia e política do dia, música, games,
infantil, Índia/Sul da Ásia, nem: {", ".join(cfg.get("tipos_ocultos") or [])}.
Não repita nenhuma destas: {", ".join(ja[:400])}.
Responda SÓ com JSON: [{{"f": "fórmula", "ex": "título de onde saiu"}}, ...]"""
        try:
            o = subprocess.run([claude_cli(), "-p", prompt, "--model", "haiku", "--output-format", "text"],
                               capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            txt = o.stdout[o.stdout.find("["):o.stdout.rfind("]") + 1]
            novas = [{"f": x["f"].strip().lower(), "ex": x.get("ex", "")} for x in json.loads(txt)
                     if x.get("f") and x["f"].strip().lower() not in ja and len(x["f"].split()) >= 3
                     and x["f"].strip().lower() not in GENERICAS]
        except Exception as e:
            print("   fórmulas da IA falharam:", e, flush=True)
            novas = []
        vivas = novas + vivas
        a = {"data": hoje, "lista": vivas[:400]}
        FORMULAS.write_text(json.dumps(a, ensure_ascii=False, indent=1))
        print(f"   fórmulas: {len(novas)} novas de {len(titulos)} títulos que estouraram; {len(vivas)} no total", flush=True)
    # ordem: as que já trouxeram canal bom primeiro, depois as novas, depois o resto
    vivas.sort(key=lambda x: (-bons.get(x["f"], 0), x["f"] in rend))
    return [x["f"] for x in vivas[:MAX_FORMULAS]]


def regiao_de(termo):
    # só quando o próprio termo cita o país; o resto vai sem país (não puxar tudo para um mercado)
    t = termo.lower()
    for chave, r in (("uk ", "GB"), ("uk", "GB"), ("canad", "CA"), ("austral", "AU")):
        if chave in t:
            return r
    return None


def buscar_api(nicho, termo):
    # vídeos longos (médios e longos) dos últimos 30 dias, mais vistos, para o público do país do termo
    depois = (HOJE - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    achados = {}
    ids = []
    for dur in ("medium", "long"):
        extra = {"regionCode": regiao_de(termo)} if regiao_de(termo) else {}
        r = api("search", part="snippet", q=termo, type="video", order="viewCount", publishedAfter=depois,
                relevanceLanguage="en", videoDuration=dur, maxResults=50, **extra)
        for it in r.get("items", []):
            ids.append(it["id"]["videoId"])
            achados.setdefault(it["snippet"]["channelId"], f"en:{nicho}:api")
    return achados


def buscar(lingua, nicho, termo, periodo):
    time.sleep(1 + hash(termo) % 3)   # devagar: o YouTube segura as buscas quando são muitas seguidas
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(termo) + "&sp=" + FILTROS[periodo]
    d = ytdlp(url, 150)
    if not d or not any(d.get("entries") or []):
        return None
    achados = {}
    for e in (d or {}).get("entries", []):
        # só vídeos longos (4 min ou mais): canal de Shorts não interessa
        if e.get("channel_id") and (e.get("view_count") or 0) >= MIN_VIEWS and (e.get("duration") or 0) >= 240:
            achados.setdefault(e["channel_id"], f"{lingua}:{nicho}:{periodo}")
            if e["view_count"] >= VIRAL and e.get("title"):   # vitrine: de onde saem as fórmulas de título
                VIRAIS.append((e["view_count"], e["title"], nicho))
    return achados


def triagem_lote(cids, com_feed=False):
    # Idade dos canais pela API OFICIAL (28/10/2026: o feed RSS público passou a responder 404 e a triagem descartava
    # os canais em silêncio). 1 ponto por 50 canais; + 1 ponto por canal com até 15 vídeos (data do 1º vídeo: conta antiga
    # que só começou a postar agora) ou quando pedem a lista de títulos (com_feed). Cota acabou → reserva: o feed RSS.
    out, cids = {}, [c for c in dict.fromkeys(cids) if c]
    try:
        canais = {}
        for i in range(0, len(cids), 50):
            for c in api("channels", part="snippet,statistics,contentDetails", id=",".join(cids[i:i + 50]), maxResults=50).get("items", []):
                canais[c["id"]] = c
        for cid, c in canais.items():
            sn = c["snippet"]
            n = int(c.get("statistics", {}).get("videoCount", 0) or 0)
            criado = sn.get("publishedAt")
            feed = []
            if com_feed or n <= 15:
                up = c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                itens = api("playlistItems", part="snippet,contentDetails", playlistId=up, maxResults=15).get("items", []) if up else []
                feed = [{"id": x["contentDetails"]["videoId"], "titulo": x["snippet"]["title"],
                         "publicado": x["contentDetails"].get("videoPublishedAt") or x["snippet"].get("publishedAt"), "views": None}
                        for x in itens]
            inicio = criado
            if feed and n < 15:   # canal antigo que só começou a postar agora também conta
                primeiro = min(v["publicado"] for v in feed if v["publicado"])
                if not criado or primeiro > criado:
                    inicio = primeiro
            out[cid] = {"nome": sn.get("title"), "criado": criado, "inicio": inicio, "feed": feed}
    except CotaAcabou:
        falta = [c for c in cids if c not in out]
        print(f"   cota da API acabou na triagem: {len(falta)} canais pelo feed RSS (reserva)", flush=True)
        with cf.ThreadPoolExecutor(12) as ex:
            for cid, t in ex.map(triagem_rss, falta):
                if t:
                    out[cid] = t
    return out


def triagem(cid):
    # um canal só (Malandro): pela API, com a lista dos últimos títulos
    return cid, triagem_lote([cid], com_feed=True).get(cid)


def triagem_rss(cid):
    xml = baixar("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid)
    if not xml:
        return cid, None
    r = ET.fromstring(xml)
    vids = []
    for e in r.findall("a:entry", NS):
        est = e.find("m:group/m:community/m:statistics", NS)
        vids.append({"id": e.find("yt:videoId", NS).text, "titulo": e.find("a:title", NS).text,
                     "publicado": e.find("a:published", NS).text,
                     "views": int(est.get("views")) if est is not None else None})
    criado = r.find("a:published", NS).text if r.find("a:published", NS) is not None else None
    # canal antigo que só começou a postar agora também conta (feed com menos de 15 vídeos = vemos o 1º vídeo)
    inicio = criado
    if vids and len(vids) < 15:
        primeiro = min(v["publicado"] for v in vids)
        if not criado or primeiro > criado:
            inicio = primeiro
    return cid, {"nome": r.find("a:title", NS).text, "criado": criado, "inicio": inicio, "feed": vids}


def ficha(cid, tri):
    longos = ytdlp(f"https://www.youtube.com/channel/{cid}/videos", 300) or {}
    curtos = {}   # Shorts não interessam (só canais de vídeo longo)
    orig = {v["id"]: v for v in tri["feed"]}
    def lista(d, short):
        out = []
        for e in d.get("entries") or []:
            o = orig.get(e.get("id"), {})
            out.append({"id": e.get("id"), "titulo": o.get("titulo") or e.get("title"), "views": e.get("view_count") or o.get("views") or 0,
                        "duracao": e.get("duration"), "short": short, "publicado": o.get("publicado")})
        return out
    vids = lista(longos, False) + lista(curtos, True)
    ids = {v["id"] for v in vids}
    # o feed também traz Shorts e lives; só entram se a lista de vídeos longos não os tiver (sem saber o tipo)
    vids += [{**v, "duracao": None, "short": None} for v in tri["feed"] if v["id"] not in ids and not longos.get("entries")]
    # o yt-dlp devolve os títulos traduzidos para inglês; os que não vieram no feed são relidos na língua do canal
    lingua = lingua_de([v["titulo"] for v in tri["feed"]] or [v["titulo"] for v in vids])
    if lingua and lingua != "en" and any(v["id"] not in orig for v in vids):
        nomes = {}
        for aba in ("videos",):
            o = subprocess.run(["yt-dlp", "--js-runtimes", "node", "--extractor-args", "youtube:lang=" + lingua,
                                "--flat-playlist", "--playlist-end", "300", "--print", "%(id)s\t%(title)s",
                                f"https://www.youtube.com/channel/{cid}/{aba}"], capture_output=True, text=True, timeout=300)
            nomes.update(l.split("\t", 1) for l in o.stdout.splitlines() if "\t" in l)
        for v in vids:
            if v["id"] not in orig and v["id"] in nomes:
                v["titulo"] = nomes[v["id"]]
    dias = max(1, (HOJE - data(tri["inicio"])).days) if tri["inicio"] else None
    lv = [v["views"] for v in vids if v["short"] is False] or [0]
    sv = [v["views"] for v in vids if v["short"]] or [0]
    total = sum(v["views"] for v in vids)
    insc = longos.get("channel_follower_count") or curtos.get("channel_follower_count")
    melhor = max(vids, key=lambda v: v["views"]) if vids else None
    return {
        "id": cid, "nome": tri["nome"], "handle": longos.get("uploader_id") or curtos.get("uploader_id"),
        "descricao": (longos.get("description") or "")[:500],
        "criado": tri["criado"], "inicio": tri["inicio"], "dias": dias,
        "inscritos": insc, "views_total": total, "n_longos": sum(1 for v in vids if v["short"] is False),
        "n_shorts": sum(1 for v in vids if v["short"]),
        "mediana_longos": int(st.median(lv)), "mediana_shorts": int(st.median(sv)),
        "views_por_dia": round(total / dias) if dias else None,
        "inscritos_por_dia": round(insc / dias, 1) if insc and dias else None,
        "melhor": melhor, "videos": sorted(vids, key=lambda v: -v["views"])[:60],
        "lingua": lingua,
    }


class CotaAcabou(Exception):
    """A cota diária da API do YouTube acabou: o robô para de usar a API e exporta o que já fez."""


def chave_api():
    for linha in (RAIZ / ".env").read_text().splitlines():
        if linha.startswith("YOUTUBE_API_KEY=") and linha.split("=", 1)[1].strip():
            return linha.split("=", 1)[1].strip()


def api(recurso, **params):
    # YouTube Data API oficial (10.000 pontos/dia; cada chamada aqui custa 1)
    u = f"https://www.googleapis.com/youtube/v3/{recurso}?" + urllib.parse.urlencode({**params, "key": chave_api()})
    for tentativa in range(3):
        try:
            return json.loads(urllib.request.urlopen(u, timeout=30).read())
        except urllib.error.HTTPError as e:
            corpo = e.read().decode("utf-8", "ignore")
            if "quotaExceeded" in corpo:
                raise CotaAcabou()
            if e.code in (400, 403, 404):
                return {}
        except Exception:
            pass
        time.sleep(3 + tentativa * 5)
    return {}


def segundos(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    d, h, mi, s = (int(x or 0) for x in m.groups()) if m else (0, 0, 0, 0)
    return d * 86400 + h * 3600 + mi * 60 + s


def fichas_api(cids, triados):
    # canais em lotes de 50 (1 ponto por lote) + 2 pontos por canal (últimos 50 vídeos e as views deles)
    canais = {}
    for i in range(0, len(cids), 50):
        for c in api("channels", part="snippet,statistics,contentDetails", id=",".join(cids[i:i + 50]), maxResults=50).get("items", []):
            canais[c["id"]] = c

    def um(cid):
        c = canais.get(cid)
        if not c:
            return None
        sn, est = c["snippet"], c.get("statistics", {})
        up = c["contentDetails"]["relatedPlaylists"]["uploads"]
        itens = api("playlistItems", part="snippet,contentDetails", playlistId=up, maxResults=50).get("items", [])
        ids = [x["contentDetails"]["videoId"] for x in itens]
        info = {v["id"]: v for v in api("videos", part="contentDetails,statistics", id=",".join(ids), maxResults=50).get("items", [])} if ids else {}
        vids = []
        for x in itens:
            vid = x["contentDetails"]["videoId"]
            v = info.get(vid)
            if not v:
                continue
            dur = segundos(v["contentDetails"].get("duration"))
            pub = x["contentDetails"].get("videoPublishedAt") or x["snippet"].get("publishedAt")
            views = int(v.get("statistics", {}).get("viewCount", 0))
            horas = max(1, (HOJE - data(pub)).total_seconds() / 3600) if pub else None
            vids.append({"id": vid, "titulo": x["snippet"]["title"], "views": views, "duracao": dur,
                         "short": dur <= 180, "publicado": pub, "vph": round(views / horas, 1) if horas else None})
        total_videos = int(est.get("videoCount", 0))
        inicio = triados.get(cid, {}).get("inicio") or sn.get("publishedAt")
        if vids and total_videos <= 50:   # temos todos os vídeos: o 1º vídeo marca o começo de verdade
            primeiro = min(v["publicado"] for v in vids if v["publicado"])
            if primeiro > (sn.get("publishedAt") or ""):
                inicio = primeiro
        dias = max(1, (HOJE - data(inicio)).days) if inicio else None
        longos = [v for v in vids if not v["short"]]
        insc = None if est.get("hiddenSubscriberCount") else int(est.get("subscriberCount", 0))
        total = int(est.get("viewCount", 0))
        lv = [v["views"] for v in longos] or [0]
        return {
            "id": cid, "nome": sn["title"], "handle": sn.get("customUrl"), "descricao": (sn.get("description") or "")[:500],
            "pais": sn.get("country"),
            "criado": sn.get("publishedAt"), "inicio": inicio, "dias": dias,
            "inscritos": insc, "views_total": total, "n_longos": len(longos), "n_shorts": len(vids) - len(longos),
            "n_videos_canal": total_videos, "mediana_longos": int(st.median(lv)), "mediana_shorts": 0,
            "views_por_dia": round(total / dias) if dias else None,
            "inscritos_por_dia": round(insc / dias, 1) if insc and dias else None,
            "melhor": max(longos, key=lambda v: v["views"]) if longos else None,
            "videos": sorted(longos, key=lambda v: -v["views"])[:60],
            "lingua": lingua_de([v["titulo"] for v in vids]) or (sn.get("defaultLanguage") or "")[:2] or None,
        }

    with cf.ThreadPoolExecutor(8) as ex:
        for f in ex.map(um, cids):
            if f:
                yield f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sementes", type=int, default=9999)
    ap.add_argument("--sem-busca", action="store_true", help="só atualiza as fichas dos canais novos já achados")
    ap.add_argument("--api-buscas", type=int, default=0, help="quantas pesquisas fazer pela API oficial (200 pontos cada)")
    ap.add_argument("--so-api", action="store_true", help="não usa a busca grátis (quando o YouTube está segurando)")
    ap.add_argument("--grupos", help="só estes grupos de sementes_en.json (nomes separados por ;), sem 'canais parecidos'")
    a = ap.parse_args()
    b = abrir_banco()
    agora = HOJE.isoformat(timespec="seconds")

    # 1. BUSCA
    candidatos, por_termo = {}, {}   # por_termo: que canais cada pesquisa trouxe (para medir o rendimento)
    if not a.sem_busca:
        if a.api_buscas:
            feitas = set(json.loads((PASTA / "api_feitas.json").read_text())) if (PASTA / "api_feitas.json").exists() else set()
            # gira por todos os grupos: 1º termo de cada grupo, depois o 2º de cada, ... (um pouco de cada por dia)
            grupos = {}
            for g, t in sementes_en():
                grupos.setdefault(g, []).append(t)
            fila = [(g, ts[i]) for i in range(max(map(len, grupos.values()))) for g, ts in grupos.items() if i < len(ts)]
            fila = [x for x in fila if x[1] not in feitas][:a.api_buscas]
            print(f"1a. busca pela API: {len(fila)} pesquisas", flush=True)
            for i, (g, t) in enumerate(fila, 1):
                try:
                    achados_api = buscar_api(g, t)
                except CotaAcabou:
                    print("   cota da API acabou na busca; sigo sem ela", flush=True)
                    break
                for c, por in achados_api.items():
                    candidatos.setdefault(c, por)
                feitas.add(t)
                (PASTA / "api_feitas.json").write_text(json.dumps(sorted(feitas)))
                if i % 10 == 0:
                    print(f"   {i}/{len(fila)} pesquisas API, {len(candidatos)} canais", flush=True)
    if not a.sem_busca and not a.so_api:
        # só inglês: as outras línguas ficam para o Método Malandro
        so_grupos = set(a.grupos.split(";")) if a.grupos else None
        rend, fora_g = rendimento(), grupos_parados()
        base = [(g, t) for g, t in sementes_en()[:a.max_sementes] if (not so_grupos or g in so_grupos) and g not in fora_g]
        if not so_grupos:
            base += [x for x in termos_ia() if x[0] not in fora_g]   # irmãos saíram da busca (28/10/2026): viraram termômetro no Malandro
        vivos = [(g, t) for g, t in dict.fromkeys(base) if not aposentado(t, rend)]
        print(f"   pesquisas: {len(vivos)} ({len(base) - len(vivos)} aposentadas por não acharem canal novo; "
              f"{len(fora_g)} nichos parados no painel)", flush=True)
        tarefas = [("en", g, t, p) for g, t in vivos for p in FILTROS]
        try:
            tarefas += [] if so_grupos else [("en", g, t, "mes") for g, t in parecidos()]
        except CotaAcabou:
            print("   cota da API acabou: sem 'canais parecidos' nesta rodada", flush=True)
        print(f"1. busca: {len(tarefas)} pesquisas…", flush=True)
        vazias_seguidas = 0
        with cf.ThreadPoolExecutor(3) as ex:
            for i, ach in enumerate(ex.map(lambda x: buscar(*x), tarefas), 1):
                if ach is not None:
                    por_termo.setdefault(tarefas[i - 1][2], set()).update(ach)
                if ach is None:
                    vazias_seguidas += 1
                    if vazias_seguidas >= 15:
                        print("   o YouTube está segurando as buscas (15 vazias seguidas); paro a busca aqui", flush=True)
                        ex.shutdown(cancel_futures=True)
                        break
                    continue
                vazias_seguidas = 0
                for c, por in ach.items():
                    candidatos.setdefault(c, por)
                if i % 20 == 0:
                    print(f"   {i}/{len(tarefas)} pesquisas, {len(candidatos)} canais", flush=True)
        # 1b. FÓRMULAS DE TÍTULO (tiradas dos títulos que estouraram nas buscas acima): o canal achado aqui fica
        # marcado "fórmula: <fórmula>" (painel e nota de qualidade de cada fórmula)
        if not so_grupos:
            formulas = formulas_do_dia()
            tf = [("en", "fórmula: " + f, f, p) for f in formulas for p in FILTROS]
            print(f"1b. fórmulas: {len(tf)} pesquisas…", flush=True)
            with cf.ThreadPoolExecutor(3) as ex:
                for i, ach in enumerate(ex.map(lambda x: buscar(*x), tf), 1):
                    if ach is None:
                        continue
                    por_termo.setdefault(tf[i - 1][2], set()).update(ach)
                    for c, por in ach.items():   # a fórmula vale mais que o assunto: é ela que aprende
                        if not candidatos.get(c, "").startswith("en:fórmula: "):
                            candidatos[c] = por
                    if i % 20 == 0:
                        print(f"   {i}/{len(tf)} pesquisas de fórmula, {len(candidatos)} canais", flush=True)
    conhecidos = {r[0]: r[1] for r in b.execute("select id, status from canais")}
    novos_p_triar = [c for c in candidatos if c not in conhecidos]
    print(f"2. triagem: {len(candidatos)} canais na busca, {len(novos_p_triar)} nunca vistos", flush=True)

    # 2. TRIAGEM
    corte = (HOJE - dt.timedelta(days=MAX_DIAS)).isoformat()
    triados = {}
    lote = triagem_lote(novos_p_triar)
    print(f"   idade lida de {len(lote)} de {len(novos_p_triar)} canais", flush=True)
    for cid, t in lote.items():
        novo = bool(t["inicio"]) and t["inicio"] >= corte
        b.execute("insert or replace into canais(id,nome,criado,inicio,status,achado_em,achado_por) values(?,?,?,?,?,?,?)",
                  (cid, t["nome"], t["criado"], t["inicio"], "novo" if novo else "velho", agora, candidatos[cid]))
        if novo:
            triados[cid] = t
    b.commit()
    if por_termo:   # rendimento: quantos canais NOVOS (até 14 dias) cada pesquisa achou; a que só repete é aposentada
        rend = rendimento()
        for termo, cids in por_termo.items():
            u, n = rend.get(termo, [0, 0])
            rend[termo] = [u + 1, n + sum(1 for x in cids if x in triados)]
        RENDE.write_text(json.dumps(rend, ensure_ascii=False))
    # canais novos de dias anteriores também são atualizados (para medir crescimento), até passarem de 6 meses
    # canais novos de rodadas anteriores: refaz a ficha a cada 3 dias (poupa a cota da API)
    tres_dias = (HOJE - dt.timedelta(hours=20)).isoformat(timespec="seconds")   # janela de 14 dias: poucos canais, atualiza todo dia
    # só os em inglês (o painel só mostra inglês; as outras línguas ficam para o Malandro)
    antigos = [c for (c,) in b.execute("select id from canais where status='novo' and inicio >= ? and "
                                       "(atualizado is null or atualizado < ?) and (lingua = 'en' or lingua is null)",
                                       (corte, tres_dias)) if c not in triados]
    if chave_api():   # a ficha pela API já acha a data do 1º vídeo sozinha: não precisa ler a idade de novo
        triados.update({cid: {} for cid in antigos})
    else:
        triados.update(triagem_lote(antigos))
    b.execute("update canais set status='velho' where status='novo' and inicio < ?", (corte,))
    print(f"3. fichas: {len(triados)} canais novos", flush=True)

    # 3. FICHA
    dia = HOJE.date().isoformat()
    # grava canal por canal: se parar no meio, o que já foi feito fica salvo e não é refeito no mesmo dia
    feitos = {r[0] for r in b.execute("select canal from fotos where dia=? and views is not null and "
                                      "canal in (select id from canais where ficha like '%\"vph\"%')", (dia,))} if chave_api() else \
             {r[0] for r in b.execute("select canal from fotos where dia=?", (dia,))}
    falta = [c for c in triados if c not in feitos]
    print(f"   {len(triados) - len(falta)} já feitos hoje, faltam {len(falta)}"
          f" ({'API oficial' if chave_api() else 'sem chave: yt-dlp'})", flush=True)
    if chave_api():
        gerador = fichas_api(falta, triados)
    else:
        ex = cf.ThreadPoolExecutor(6)
        gerador = ex.map(lambda c: ficha(c, triados[c]), falta)
    try:
        for i, f in enumerate(gerador, 1):
            b.execute("update canais set ficha=?, lingua=?, handle=?, atualizado=? where id=?",
                      (json.dumps(f, ensure_ascii=False), f["lingua"], f["handle"], agora, f["id"]))
            b.execute("insert or replace into fotos values(?,?,?,?,?,?)",
                      (f["id"], dia, f["inscritos"], f["views_total"], f["n_longos"], f["n_shorts"]))
            b.commit()
            if i % 50 == 0:
                print(f"   {i}/{len(falta)} fichas", flush=True)
    except CotaAcabou:
        print("   cota diária da API acabou: o resto das fichas fica para a próxima rodada (o feito está salvo)", flush=True)

    # 4. EXPORTA (o tipo de conteúdo vem do scripts/classificar.py, que roda depois e reexporta)
    tem_tipo = "tipo" in [r[1] for r in b.execute("pragma table_info(canais)")]
    tipos = dict(b.execute("select id, tipo from canais where tipo is not null")) if tem_tipo else {}
    tem_prod = "producao" in [r[1] for r in b.execute("pragma table_info(canais)")]
    prods = {r[0]: r[1:] for r in b.execute("select id, producao, adapta from canais where producao is not null")} if tem_prod else {}
    saida = []
    for cid, ficha_txt, achado_em, achado_por in b.execute("select id, ficha, achado_em, achado_por from canais where status='novo' and ficha is not null"):
        f = json.loads(ficha_txt)
        fotos = b.execute("select dia, inscritos, views from fotos where canal=? order by dia", (cid,)).fetchall()
        f.update(achado_em=achado_em, achado_por=achado_por, historico=fotos, tipo=tipos.get(cid),
                 producao=(prods.get(cid) or (None, None))[0], adapta=(prods.get(cid) or (None, None))[1])
        saida.append(f)
    saida.sort(key=lambda f: -(f["views_por_dia"] or 0))
    (PASTA / "canais.json").write_text(json.dumps(saida, ensure_ascii=False))
    exportar_painel(saida)
    print(f"4. pronto: {len(saida)} canais novos em {PASTA / 'canais.json'}")


# Fora do foco: Sul e Sudeste da Ásia, África, fazendas de "survival", games e infantil
PAISES_FORA = {"IN", "PK", "BD", "NP", "LK", "KH", "VN", "ID", "PH", "MM", "TH", "MY", "NG", "KE", "GH", "ET", "UG", "EG", "SA", "AE"}
ESCRITA_FORA = re.compile(r"[\u0600-\u06ff\u0900-\u0dff\u0e00-\u0e7f\u1780-\u17ff]")   # árabe/urdu, indianas, tailandês, khmer
PALAVRAS_FORA = re.compile(r"survival|bushcraft|#outdoor|primitive|\bgta\b|minecraft|roblox|free ?fire|fortnite|gameplay|"
                           r"\bgaming\b|\bgamer\b|\bgame\b|escape|nursery|cocomelon|\bkids?\b|cartoon|desenhos|dibujos|"
                           r"\btoys?\b|mcqueen|cute animals|\bbaby\b|daily english|learn english|live ?stream|\bhindi\b|"
                           r"\burdu\b|bollywood|\bipl\b|cricket|challenge|prank|\bindia\b|\bindian\b|biryani|\bdesi\b|\bhindu\b|tamil|telugu|punjabi|pakistan", re.I)


def fora_do_foco(f, pais):
    if pais in PAISES_FORA:
        return "país " + pais
    vids = f.get("videos", [])
    titulos = [v.get("titulo") or "" for v in vids] + [f.get("nome") or ""]
    if sum(bool(ESCRITA_FORA.search(t)) for t in titulos) >= max(1, len(titulos) // 4):
        return "escrita"
    # palavra proibida no maior vídeo, no nome do canal ou em 1/5 dos títulos
    maior = vids[0].get("titulo", "") if vids else ""
    if PALAVRAS_FORA.search(maior) or PALAVRAS_FORA.search(f.get("nome") or "") or \
            sum(bool(PALAVRAS_FORA.search(t)) for t in titulos) >= max(2, len(titulos) // 5):
        return "palavras"
    return None


def _escala(x, piso=1000, teto_log=2.5):
    # 0 a 1 em escala de 10x: piso (1 mil) = 0; 1 mil x 10^2,5 (~316 mil) = 1
    import math
    return max(0.0, min(1.0, (math.log10(max(x, 1)) - math.log10(piso)) / teto_log))


def bombando(views_longos, dias):
    # Nota 0-100 de "o canal está estourando de verdade" (28/10/2026):
    #   40% força (mediana de views dos vídeos longos), 30% velocidade (views por dia), 30% repetição (quantos vídeos
    #   passaram de 10 mil; 3 ou mais = cheio). Um sucesso só (caso Prima Historian) fica com nota baixa.
    if not views_longos or not dias:
        return 0, 0
    hits = sum(v >= 10000 for v in views_longos)
    nota = 100 * (0.4 * _escala(st.median(views_longos)) + 0.3 * _escala(sum(views_longos) / dias)
                  + 0.3 * min(1, hits / 3))
    return round(nota), hits


# replicável (IA no classificar.py): o foco é narração + imagens/mapas; filmagem própria e rosto não.
# Formato que só funciona no país de origem dá "livre" falso no Malandro (Checkout Revolt, aposentados do Reino Unido).
FATOR_PRODUCAO = {"narração com imagens": 1.0, "animação simples": 0.7, "filmagem própria ou rosto": 0.2}
FATOR_ADAPTA = {"universal": 1.0, "adaptável ao país": 1.0, "só funciona lá": 0.4}


def oportunidade(bomb, producao, adapta):
    return round(bomb * FATOR_PRODUCAO.get(producao, 0.8) * FATOR_ADAPTA.get(adapta, 0.9))


def grupos_painel():
    # cada nicho de pesquisa: quantas pesquisas tem (fixas + da IA), quantas foram aposentadas e quantos canais novos já achou
    s = json.loads((PASTA / "sementes_en.json").read_text())
    try:
        auto = json.loads(AUTO.read_text()).get("termos", [])
    except Exception:
        auto = []
    rend = rendimento()
    out = {}
    for g, termos in [(g, t) for g, t in s.items() if not g.startswith("_")]:
        out[g] = {"termos": list(termos)}
    for g, t in auto:
        out.setdefault(g, {"termos": []})["termos"].append(t)
    for g, x in out.items():
        x["n"] = len(x["termos"])
        x["aposentados"] = sum(aposentado(t, rend) for t in x["termos"])
        x["novos"] = sum(rend.get(t, [0, 0])[1] for t in x["termos"])
        x["exemplos"] = x.pop("termos")[:6]
    return out


def exportar_painel(canais):
    # versão enxuta para o painel online (estudos/cacador/painel/dados.json): só vídeo longo e só as 9 línguas.
    # "vpd" = views por dia contando SÓ os vídeos longos (o total do canal inclui Shorts e engana)
    s = json.loads((PASTA / "sementes.json").read_text())
    nichos = {t: s["_traducoes"]["pt"].get(t, t) for t in s["en"]}
    arq_paises = PASTA / "paises.json"   # país dos canais fichados antes de a ficha guardar o país
    paises = json.loads(arq_paises.read_text()) if arq_paises.exists() else {}
    out = []
    for f in canais:
        longos = [v for v in f.get("videos", []) if v.get("short") is False]
        if f.get("inicio"):   # idade de hoje, não a do dia em que a ficha foi feita
            f = {**f, "dias": max(1, (HOJE - data(f["inicio"])).days)}
        if not f.get("dias") or f["dias"] > MAX_DIAS or not longos or f.get("lingua") != "en":
            continue
        if fora_do_foco(f, f.get("pais", paises.get(f["id"]))):
            continue
        if sum(v["views"] for v in longos) / f["dias"] < 200:   # canal que quase não anda: fora do painel (arquivo mais leve)
            continue
        vl = sum(v["views"] for v in longos)
        bomb, hits = bombando([v["views"] for v in longos], f["dias"])
        ap = f.get("achado_por") or "::"   # "língua:nicho:período"; o nicho pode ter ":" ("parecidos: Canal X")
        l, n, p = ap.split(":", 1)[0], ap.split(":", 1)[1].rsplit(":", 1)[0], ap.rsplit(":", 1)[1]
        out.append({
            "id": f["id"], "nome": f["nome"], "handle": f["handle"], "lingua": f["lingua"], "nicho": n, "tipo": f.get("tipo"),
            "dias": f["dias"], "inicio": (f["inicio"] or "")[:10], "achado": (f.get("achado_em") or "")[:10],
            "insc": f["inscritos"], "views": vl, "vpd": round(vl / f["dias"]),
            "ipd": f["inscritos_por_dia"], "nl": len(longos), "medl": f["mediana_longos"],
            "desc": (f.get("descricao") or "")[:160],
            "bomb": bomb, "hits": hits, "prod": f.get("producao"), "adapta": f.get("adapta"),
            "op": oportunidade(bomb, f.get("producao"), f.get("adapta")),
            "hist": f.get("historico", [])[-30:],
            "videos": [{"id": v["id"], "t": v["titulo"], "v": v["views"], "d": v["duracao"],
                        "p": (v.get("publicado") or "")[:10], "h": v.get("vph")} for v in longos[:12]],
        })
    out.sort(key=lambda f: -f["vpd"])
    for f in out:   # nicho de semente que já saiu da lista (ex.: survival) continua com nome em português
        nichos.setdefault(f["nicho"], s["_traducoes"]["pt"].get(f["nicho"], f["nicho"]))
    (PASTA / "painel").mkdir(exist_ok=True)
    # caractere "�" (texto quebrado na origem) é recusado pelo publicador: sai do arquivo
    (PASTA / "painel" / "dados.json").write_text(json.dumps(
        {"gerado": HOJE.isoformat(timespec="minutes"), "nichos": nichos, "canais": out,
         # nichos de pesquisa (aba Explorar: "Onde o radar procura", com botão de parar) e o rendimento de cada um
         "grupos": grupos_painel(), "tipo_grupo": TIPO_GRUPO},
        ensure_ascii=False, separators=(",", ":")).replace("\ufffd", ""))


if __name__ == "__main__":
    main()
