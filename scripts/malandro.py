# Método Malandro: em qual língua vale a pena fazer a versão de um canal (ou de um tema) que estourou?
# Uso: python3 scripts/malandro.py --canal @PeterHarrisonUK [UC... ...]   (modo principal: um canal inteiro)
#      python3 scripts/malandro.py "The Entire Story of Medusa|Medusa"     (um tema solto)
#
# MODO CANAL: procura CANAIS parecidos em cada língua, não títulos iguais (tradução literal quase nunca acha).
#   1. tema do canal = expressões que mais se repetem nos títulos (ex.: "chinese cars", "byd", "electric")
#   2. em cada língua, pesquisa essas expressões traduzidas (vídeos do último ano, 4+ min, na língua de verdade)
#   3. cada canal achado ganha nota de TEMA (quanto dos últimos títulos fala do mesmo) e de ESTRUTURA
#      (número, CAPS, travessão, !/?, tamanho) comparada com o canal de referência; os dois altos = "gêmeo"
#   4. língua sem gêmeo e com views no tema = livre e com público.
#
# MODO TEMA:
# Para cada tema: traduz para as 9 línguas do caçador, pesquisa no YouTube os vídeos do ÚLTIMO ANO e conta só
# os que estão de fato naquela língua (a busca mistura línguas e traduz títulos; o título original vem do oEmbed
# e a língua é detectada pelo texto). Só entram vídeos cujo título cita o assunto. Os maiores de cada língua
# ganham a idade do canal (canal novo = concorrente que pode crescer rápido). Grava o resultado em estudos/cacador/painel/malandro.json (aba do painel).
import concurrent.futures as cf, datetime as dt, json, re, statistics as st, subprocess, sys, time, unicodedata, urllib.parse, urllib.request
from pathlib import Path

from langdetect import DetectorFactory, detect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cacador import HOJE, LINGUAS as LINGUAS_RADAR, MAX_DIAS, PASTA, api, data, fichas_api, traduzir, triagem, ytdlp

# línguas do Malandro (28/10/2026): ranking falantes^0,7 × RPM médio da língua, com desconto de quem vê em inglês
# (estudos/linguas-2026-10-28/conta.py e ingles.py), corte "até tcheco"; o usuário tirou o turco (paga pouco demais).
# Turco, grego, húngaro: só com --linguas.
LINGUAS = LINGUAS_RADAR + ["nl", "sv", "no", "da", "fi", "cs"]

DetectorFactory.seed = 0
ANO = "EgQIBRAB"   # filtro: só vídeos, enviados este ano
RECENTE = "CAISBAgEEAE="   # só vídeos deste mês, do mais novo para o mais velho
SAIDA = PASTA / "painel" / "malandro.json"
SAIDA.parent.mkdir(parents=True, exist_ok=True)


def titulo_original(vid):
    try:
        u = "https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + vid
        return json.loads(urllib.request.urlopen(u, timeout=15).read())["title"]
    except Exception:
        return None


def lingua(t):
    try:
        l = detect(t)
        return {"sh": "hr", "bs": "hr", "sr": "hr"}.get(l, l)   # croata, bósnio e sérvio (latino) se confundem
    except Exception:
        return None


def veredito(n, media):
    if n < 4:
        return "Brecha: quase ninguém fez"
    if n < 12 and media >= 30000:
        return "Bom: poucos fizeram e deu views"
    if media >= 100000:
        return "Concorrido, mas o público é grande"
    if media < 10000:
        return "Pouca procura"
    return "Já tem gente fazendo"


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def raiz(p):
    # "Meduzi", "Meduza", "Medusei" → "medu": pega o começo da palavra para aceitar as declinações
    p = sem_acento(p)
    return p[:max(4, len(p) - 3)] if len(p) > 5 else p


def palavras(t):
    return set(re.findall(r"\w{3,}", sem_acento(t)))


def uma_lingua(titulo, chave, l):
    busca = titulo if l == "en" else traduzir(titulo, l)[0]
    chave_l = chave if l == "en" else traduzir(chave, l)[0]
    raizes = [raiz(p) for p in re.findall(r"\w+", chave_l) if len(p) >= 3]
    vistos, nessa, falhou = set(), [], False
    # duas buscas: o título traduzido e só o assunto (pega quem fez o tema com outro título)
    for q in (busca, chave_l):
        d = None
        for espera in (0, 20, 60):   # resposta vazia = YouTube freando: espera e tenta de novo
            time.sleep(espera)
            d = ytdlp("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q) + "&sp=" + ANO, 60)
            if d and any(d.get("entries") or []):
                break
            d = None
        if d is None:
            falhou = True
            continue
        # só vídeos de 4 min ou mais: tira músicas e Shorts
        es = [e for e in d.get("entries", []) if e.get("id") and e["id"] not in vistos and (e.get("duration") or 0) >= 240]
        vistos.update(e["id"] for e in es)
        with cf.ThreadPoolExecutor(8) as ex:
            titulos = list(ex.map(titulo_original, [e["id"] for e in es]))
        for e, t in zip(es, titulos):
            if not t or lingua(t) != l:
                continue
            tt = sem_acento(t)
            if not all(r in tt for r in raizes):   # o título precisa falar do assunto
                continue
            nessa.append({"id": e["id"], "titulo": t, "views": e.get("view_count") or 0, "canal": e.get("channel"),
                          "canal_id": e.get("channel_id")})
    # título parecido: divide boa parte das palavras com o título traduzido
    pb = palavras(busca)
    for v in nessa:
        v["parecido"] = len(pb & palavras(v["titulo"])) / max(1, len(pb)) >= 0.5
    nessa.sort(key=lambda v: -v["views"])
    for v in nessa[:5]:   # idade do canal dos maiores
        t = triagem(v["canal_id"])[1] if v.get("canal_id") else None
        v["dias_canal"] = (HOJE - data(t["inicio"])).days if t and t["inicio"] else None
        v["canal_novo"] = v["dias_canal"] is not None and v["dias_canal"] <= MAX_DIAS
    media = round(st.mean(v["views"] for v in nessa[:10])) if nessa else 0
    return {"lingua": l, "busca": busca, "chave": chave_l, "recentes": len(nessa),
            "parecidos": sum(v["parecido"] for v in nessa), "media_top": media,
            "canais": len({v["canal"] for v in nessa}), "top": nessa[:5],
            "veredito": "Erro na pesquisa: rodar de novo" if falhou else veredito(len(nessa), media)}


def analisar(titulo, chave):
    res = [uma_lingua(titulo, chave, l) for l in LINGUAS]   # uma língua por vez, para não forçar o YouTube
    return {"tema": titulo, "chave": chave, "data": dt.date.today().strftime("%d/%m/%Y"), "linguas": res}


# palavras que não dizem nada sobre o assunto (em várias línguas)
VAZIAS = set("""that this with what from they your have just about here they're these those there their will them were been more most
when then than into only also even every after before nobody anyone everyone never ever still really why how the and for are you
que los las por una para con del como pero mas más este esta esto todo todos nunca sobre porque quien
les des pour dans avec sont plus tout tous cette mais comme pourquoi
der die das und mit von für nicht sind eine einen auch noch warum wie
che per con della delle sono questo questa tutti perché come
care sunt pentru din cum este acest aceasta toate
koji koja koje kako zašto nije samo
jest się nie jak dlaczego które który tylko wszystko""".split())


def chaves(t):
    # raízes das palavras que carregam o assunto (4+ letras, sem as vazias)
    return {raiz(p) for p in re.findall(r"\w{4,}", sem_acento(t)) if p not in VAZIAS and not p.isdigit()}


def parecido(titulo_achado, *referencias):
    # fração das palavras-chave da referência que aparecem no título achado (vale a melhor referência)
    ta = sem_acento(titulo_achado)
    melhor = 0
    for ref in referencias:
        ks = chaves(ref)
        if ks:
            melhor = max(melhor, sum(k in ta for k in ks) / len(ks))
    return melhor


TRAD = PASTA / "traducoes_cache.json"


def traduzir_lote(titulos, l):
    try:
        cache = json.loads(TRAD.read_text()) if TRAD.exists() else {}
    except Exception:
        cache = {}
    falta = [t for t in dict.fromkeys(titulos) if f"{l}|{t}" not in cache]
    if falta:
        for t, tr in zip(falta, _traduzir_lote(falta, l)):
            if tr:
                cache[f"{l}|{t}"] = tr
        TRAD.write_text(json.dumps(cache, ensure_ascii=False))
    return [cache.get(f"{l}|{t}") for t in titulos]


def _traduzir_lote(titulos, l):
    try:
        linhas = traduzir("\n".join(titulos), l)[0].split("\n")
        if len(linhas) == len(titulos):
            return [x.strip() for x in linhas]
    except Exception:
        pass
    out = []
    for t in titulos:   # reserva: um por um
        try:
            out.append(traduzir(t, l)[0])
        except Exception:
            out.append(None)
        time.sleep(1)
    return out


def similares(original, traduzido, l):
    # pesquisa o título traduzido no YouTube daquela língua e devolve os vídeos parecidos que estão de fato nela.
    # Com a busca em inglês o YouTube às vezes mostra o título traduzido para o inglês: compara com os dois.
    d = None
    for espera in (2, 20, 60):
        time.sleep(espera)
        d = ytdlp("https://www.youtube.com/results?search_query=" + urllib.parse.quote(traduzido) + "&sp=" + ANO, 40)
        if d and any(d.get("entries") or []):
            break
        d = None
    if d is None:
        return None
    candidatos = [e for e in d["entries"] if e and e.get("id") and (e.get("duration") or 0) >= 240
                  and parecido(e.get("title") or "", original, traduzido) >= 0.4]
    achados = []
    for e in candidatos:
        t = titulo_original(e["id"])
        if t and lingua(t) == l and parecido(t, original, traduzido) >= 0.4:
            achados.append({"id": e["id"], "titulo": t, "views": e.get("view_count") or 0, "canal": e.get("channel")})
    return sorted(achados, key=lambda v: -v["views"])


# palavras de gancho e de país: dizem "como" o título fala, não "do que" fala
GANCHOS = set("""disaster bombshell panic shocked shocking nobody coming finally quietly real reason biggest entire inside found
here why worst best never exposed terrify terrifying fear fears silence breaking truth secret secrets hidden revealed reveal
nightmare collapse collapsing crashing crash destroy destroyed survive won't can't don't doesn't isn't aren't actually
just going wrong unbelievable insane huge massive warning warns everyone everything something thousands millions
britain british britons america american americans canada canadian canadians australia australian europe european
here's across against within without because explained finally really""".split())
PAISES_CURTOS = {"uk", "us", "usa", "eu"}


def temas_do_canal(titulos, n_duplas=2, n_simples=2):
    # expressões que mais se repetem nos títulos (ex.: "chinese cars", "byd", "electric"): é o TEMA do canal
    simples, duplas, forma = {}, {}, {}
    for t in titulos:
        ws = [w for w in re.findall(r"[A-Za-zÀ-ÿ']+", t.replace("’", "'"))]
        cont = [w for w in ws if sem_acento(w) not in VAZIAS and sem_acento(w) not in GANCHOS
                and sem_acento(w) not in PAISES_CURTOS and (len(w) >= 4 or (w.isupper() and len(w) >= 2))]
        vistos = set()
        for w in cont:
            k = raiz(w)
            if k not in vistos:
                simples[k] = simples.get(k, 0) + 1
                vistos.add(k)
            forma.setdefault(k, w.lower())
        for a, b in zip(ws, ws[1:]):
            if a in cont and b in cont:
                k = (raiz(a), raiz(b))
                duplas[k] = duplas.get(k, 0) + 1
                forma.setdefault(k, f"{a.lower()} {b.lower()}")
    top_duplas, usadas = [], set()
    for k, c in sorted(duplas.items(), key=lambda x: -x[1]):
        if c >= 2 and len(top_duplas) < n_duplas and not (set(k) & usadas):   # sem sobreposição ("engineers tore"/"tore down")
            top_duplas.append(k)
            usadas |= set(k)
    top_simples = [k for k, c in sorted(simples.items(), key=lambda x: -x[1]) if c >= 2 and k not in usadas][:n_simples]
    return [forma[k] for k in top_duplas + top_simples]


# palavras de gancho (o "jeito" dos títulos virais). A fórmula de um canal = as que ele usa com frequência
GANCHO_EN = """nobody everyone everybody never wants regret avoid worst best banned illegal rotting collapsing collapse
crashing crash disaster bombshell panic shocked shocking terrify terrifying destroy destroyed survive secret secrets hidden
truth real reason finally quietly exposed warning warns hate hates stop dumping returning refusing ditching abandoned
failing worthless cheap cheapest expensive mistake mistakes wrong nightmare unbelievable insane biggest breaking silence
already actually forever entire everything nothing doctors mechanics experts dealers scientists engineers reveal revealed
found inside behind""".split()


def formula_do_canal(titulos, n=12):
    cont = {}
    for t in titulos:
        tt = sem_acento(t.replace("’", "'"))
        for g in GANCHO_EN:
            if re.search(r"\b" + g, tt):
                cont[g] = cont.get(g, 0) + 1
    return [g for g, c in sorted(cont.items(), key=lambda x: -x[1]) if c >= 2][:n]


def estrutura(titulos):
    # COMO o canal escreve (vale em qualquer língua): começa com número, tem número, palavra em CAPS,
    # título em duas partes (afirmação — gancho), termina com ! ou ?, tamanho médio
    if not titulos:
        return None
    n = len(titulos)
    return [sum(bool(re.match(r"\s*\d", t)) for t in titulos) / n,
            sum(bool(re.search(r"\d", t)) for t in titulos) / n,
            sum(bool(re.search(r"\b[A-ZÀ-Ý]{4,}\b", t)) for t in titulos) / n,
            sum(bool(re.search(r"\S\s*[—–|:]\s*\S| - ", t)) for t in titulos) / n,
            sum(t.rstrip()[-1:] in "!?" for t in titulos if t.strip()) / n,
            min(1, st.mean(len(t) for t in titulos) / 100)]


def sim_estrutura(a, b):
    return round(1 - st.mean(abs(x - y) for x, y in zip(a, b)), 2) if a and b else 0


def no_tema(t, buscas):
    return any(parecido(t, q) >= 0.5 for q in buscas)


CACHE = PASTA / "malandro_cache.json"   # buscas do dia: canais do mesmo nicho reaproveitam


def buscar_cache(q, l, sp=ANO):
    # sp=RECENTE: os mais novos do mês, para achar canal recém-criado que ainda não tem views para aparecer
    # na busca por relevância (uma cópia em romeno nasceu uma semana depois da análise)
    chave = f"{dt.date.today()}|{l}|{q}" + ("" if sp == ANO else f"|{sp}")
    try:
        cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    except Exception:
        cache = {}
    if chave in cache:
        return cache[chave]
    d = None
    for espera in (1, 20, 60):
        time.sleep(espera)
        d = ytdlp("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q) + "&sp=" + urllib.parse.quote(sp), 40)
        if d and any(d.get("entries") or []):
            break
        d = None
    if d is None:
        return None
    es = [e for e in d["entries"] if e and e.get("id") and (e.get("duration") or 0) >= 240]
    # título da busca já na língua certa = original; só nos outros confere o original (o YouTube traduz alguns)
    precisa = [e for e in es if lingua(e.get("title") or "") != base(l)]
    with cf.ThreadPoolExecutor(8) as ex:
        originais = dict(zip([e["id"] for e in precisa], ex.map(titulo_original, [e["id"] for e in precisa])))
    res = [{"id": e["id"], "titulo": originais.get(e["id"], e.get("title")), "views": e.get("view_count") or 0,
            "canal": e.get("channel"), "canal_id": e.get("channel_id")} for e in es]
    try:
        cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    except Exception:
        cache = {}
    cache = {k: v for k, v in cache.items() if k.startswith(str(dt.date.today()))}
    cache[chave] = res
    CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return res


NOMES = {"en": "inglês", "pt": "português", "es": "espanhol", "fr": "francês", "de": "alemão", "it": "italiano",
         "ro": "romeno", "hr": "croata", "pl": "polonês",
         # línguas extras: só quando pedidas com --linguas (o radar diário continua nas 9 de cima)
         "nl": "holandês", "sv": "sueco", "da": "dinamarquês", "no": "norueguês", "fi": "finlandês", "cs": "tcheco",
         "sk": "eslovaco", "hu": "húngaro", "el": "grego", "tr": "turco", "bg": "búlgaro", "uk": "ucraniano",
         "lt": "lituano", "lv": "letão", "et": "estoniano", "sl": "esloveno"}
# países (30/10/2026): mesma língua, outro mercado ("en-GB" = inglês sobre o Reino Unido). Para formato regional como o
# Exemplo: o original é sobre os EUA; uma versão sobre o Reino Unido ou o Chile só está ocupada se alguém já a fez.
PAISES = {"en-GB": "Reino Unido", "en-CA": "Canadá", "en-AU": "Austrália", "en-IE": "Irlanda", "en-NZ": "Nova Zelândia",
          "en-ZA": "África do Sul", "es-MX": "México", "es-AR": "Argentina", "es-CL": "Chile", "es-CO": "Colômbia",
          "pt-PT": "Portugal", "fr-CA": "Canadá (Quebec)", "en-JP": "Japão"}
NOMES.update({k: f"{NOMES[k[:2]]} sobre o país {v} (para o público de lá)" for k, v in PAISES.items()})


def base(l):
    return l.split("-")[0]   # "es-CL" → "es": a língua dos títulos


def claude_cli():
    # o Claude Code instalado no computador ou o que vem na extensão do VS Code; usa o login (assinatura), sem chave de API
    import glob, shutil
    if shutil.which("claude"):
        return shutil.which("claude")
    # ~/.vscode/extensions no Mac; ~/.vscode-server/extensions no Linux/WSL do Windows (VS Code conectado ao WSL)
    achados = sorted(glob.glob(str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")) +
                     glob.glob(str(Path.home() / ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude")),
                     key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("claude-code-")[1].split("/")[0])])
    return achados[-1] if achados else None


def pesquisas_ia(nome, titulos, linguas):
    # a IA entende o nicho e a fórmula do canal e escreve pesquisas NATURAIS em cada língua, já adaptadas à região
    # (ex.: "[Estado] Is Hiding Something…" vira "o que Minas Gerais esconde" em português)
    cli = claude_cli()
    if not cli:
        return None
    prompt = f"""Canal do YouTube em inglês: "{nome}". Títulos (com views):
{chr(10).join("- " + t for t in titulos[:25])}

1) Descreva em uma frase o nicho e a fórmula de título do canal.
2) Para cada língua {", ".join(f"{l} ({NOMES[l]})" for l in linguas)}, escreva 3 pesquisas curtas (2 a 6 palavras) que um
falante nativo digitaria no YouTube e que trariam vídeos/canais fazendo ESSE MESMO TIPO de conteúdo naquela língua,
adaptado à realidade local (lugares, marcas, pessoas do país) quando o original é regional. Nada de tradução literal.
Depois dessas 3, uma 4ª pesquisa: a FÓRMULA do título (só a parte fixa, sem o assunto) do jeito que um copiador nativo a
escreveria (ex.: "Who Really Makes X for Y – The Answer May Surprise You" → pt "quem fabrica de verdade", de "wer stellt
wirklich her"). 29/10/2026: sem ela, o Malandro deu "livre" em português para um formato que já tinha cópia com 216 mil views.

Responda SÓ com JSON: {{"nicho": "...", "buscas": {{"pt": ["...", "...", "...", "<fórmula>"], ...}}}}"""
    try:
        o = subprocess.run([cli, "-p", prompt, "--model", "sonnet", "--output-format", "text"],
                           capture_output=True, text=True, timeout=240)
        txt = o.stdout[o.stdout.find("{"):o.stdout.rfind("}") + 1]
        r = json.loads(txt)
        return r if r.get("buscas") else None
    except Exception:
        return None


def juiz(ref_nome, ref_titulos, l, canais):
    # a IA lê os títulos (em qualquer língua) e diz quais canais copiam o modelo do original
    cli = claude_cli()
    if not cli or not canais:
        return None
    pais = (f"\nAqui o mercado é um PAÍS ({PAISES[l]}): só é gemeo/primo/vizinho o canal que faz isso SOBRE {PAISES[l]}. "
            f"Canal que faz o mesmo sobre outro país (inclusive o próprio original e cópias dele sobre os EUA ou a Espanha) é \"nao\"."
            if l in PAISES else "")
    blocos = "\n\n".join(f"[{i}] {c['nome']} ({c['dias'] if c['dias'] is not None else '?'} dias)\n" +
                          "\n".join("- " + t for t in c["titulos"][:10]) for i, c in enumerate(canais))
    prompt = f"""Você avalia canais do YouTube. Canal ORIGINAL em inglês: "{ref_nome}". Títulos dele:
{chr(10).join("- " + t for t in ref_titulos[:15])}

Abaixo, canais em {NOMES.get(l, l)}. Para cada um, diga se ele é uma CÓPIA/ADAPTAÇÃO do modelo do original:
- "gemeo": mesmo tema E mesma fórmula de título (mesmo tipo de gancho, listas numeradas, mesma promessa), adaptado à língua/país;
- "primo": mesma fórmula de título aplicada a outro tema do MESMO nicho (variação regional);
- "vizinho": ainda NÃO copia, mas é um canal do mesmo assunto bem específico do original (ex.: o original é geografia
  escondida de regiões e este canal faz geografia/curiosidades das regiões do país), focado nisso e ativo: pode virar
  cópia a qualquer momento só trocando a frase do título;
- "nao": canal comum do assunto (notícia, review, vlog, canal tradicional) sem a fórmula do original, ou outro nicho.
Seja rigoroso: falar do mesmo assunto NÃO basta, e usar palavras genéricas como "segredo", "mistério" ou listas numeradas
também NÃO basta; tem que copiar a fórmula específica do original (a mesma frase-molde do título adaptada) ou o mesmo
conceito de canal. Canais antigos e generalistas que só às vezes tocam no tema são "nao".{pais}

{blocos}

Responda SÓ com JSON, sem texto fora dele: [{{"i": 0, "veredito": "gemeo|primo|vizinho|nao", "motivo": "até 12 palavras em português"}}, ...]"""
    try:
        o = subprocess.run([cli, "-p", prompt, "--model", "haiku", "--output-format", "text"],
                           capture_output=True, text=True, timeout=180)
        txt = o.stdout[o.stdout.find("["):o.stdout.rfind("]") + 1]
        return {x["i"]: x for x in json.loads(txt)}
    except Exception:
        return None


def nossos_canais():
    # os seus canais (estudos/cacador/nossos_canais.json, lista de IDs UC...): nunca contam como cópia
    try:
        return {k for k in json.loads((PASTA / "nossos_canais.json").read_text()) if k.startswith("UC")}
    except Exception:
        return set()


def lingua_canal(l, buscas, est_ref, nicho, formula, dias_ref, ref_nome=None, ref_titulos=None, ref_id=None):
    # pesquisa os temas naquela língua, junta os CANAIS que aparecem e mede quanto cada um parece o canal de referência
    videos, falhas, novos_ids = [], 0, set()
    for q in buscas:
        res = buscar_cache(q, l)
        if res is None:
            falhas += 1
            continue
        recentes = buscar_cache(q, l, RECENTE) or []
        novos_ids |= {v["id"] for v in recentes}
        for v in res + recentes:
            # só vídeos do assunto: o título precisa dividir palavras-chave com a pesquisa (sem isso entravam MrBeast,
            # música etc. e o "tem público" ficava falso, como no Checkout Revolt em 25/10/2026)
            if v["titulo"] and v["canal_id"] != ref_id and lingua(v["titulo"]) == base(l) and any(parecido(v["titulo"], q) >= 0.34 for q in buscas):
                videos.append(v)
    if falhas == len(buscas):
        return None
    unicos = {v["id"]: v for v in videos}
    videos = sorted(unicos.values(), key=lambda v: -v["views"])
    por_canal = {}
    for v in videos:
        if v["canal_id"]:
            por_canal.setdefault(v["canal_id"], []).append(v)
    candidatos = sorted(por_canal.items(), key=lambda x: -sum(v["views"] for v in x[1]))[:12]
    # canais que só apareceram na busca dos mais novos entram mesmo com poucas views (até 4 a mais)
    ja = {c for c, _ in candidatos}
    candidatos += [(c, vs) for c, vs in por_canal.items() if c not in ja and any(v["id"] in novos_ids for v in vs)][:4]
    with cf.ThreadPoolExecutor(6) as ex:
        feeds = dict(ex.map(triagem, [c for c, _ in candidatos]))
    canais = []
    for cid, vs in candidatos:
        fd = feeds.get(cid)
        tits = [x["titulo"] for x in fd["feed"]] if fd else [v["titulo"] for v in vs]
        tema = round(sum(no_tema(t, buscas) for t in tits) / max(1, len(tits)), 2)
        do_nicho = round(sum(any(k in sem_acento(t) for k in nicho) for t in tits) / max(1, len(tits)), 2)
        est = sim_estrutura(estrutura(tits), est_ref)
        dias = (HOJE - data(fd["inicio"])).days if fd and fd.get("inicio") else None
        # fórmula = títulos com 2+ palavras do gancho do original (traduzidas): é o que denuncia a cópia
        form = round(sum(any(k in sem_acento(t) for k in formula) for t in tits) / max(1, len(tits)), 2)
        gemeo = tema >= 0.3 and form >= 0.3 and est >= 0.6     # mesmo tema + mesma fórmula
        primo = do_nicho >= 0.4 and form >= 0.3 and est >= 0.6  # mesmo nicho (variação regional) + mesma fórmula
        # a idade não decide (cópia às vezes usa canal antigo reaproveitado), só aparece para vocês julgarem
        canais.append({"id": cid, "nome": vs[0]["canal"], "tema": tema, "nicho": do_nicho, "formula": form,
                       "estrutura": est, "dias": dias, "views_tema": sum(v["views"] for v in vs),
                       "copia": gemeo or primo, "tipo": "gêmeo" if gemeo else "primo" if primo else None,
                       "titulos": tits[:5], "antigo": False})
    # juiz: a IA confirma ou descarta (as notas acima viram só pré-filtro e plano B se a IA falhar)
    canais.sort(key=lambda c: (-(c["tema"] + c["nicho"] + c["formula"]), -c["views_tema"]))
    # o juiz vê os 8 mais parecidos + até 3 canais novos (até 60 dias) que ficariam de fora
    canais = canais[:8] + [c for c in canais[8:] if c["dias"] is not None and c["dias"] <= 60][:3]
    veredito_ia = juiz(ref_nome, ref_titulos, l, canais) if ref_titulos else None
    if veredito_ia is not None:
        for i, c in enumerate(canais):
            v = veredito_ia.get(i, {})
            c["tipo"] = {"gemeo": "gêmeo", "primo": "primo"}.get(v.get("veredito"))
            c["copia"] = c["tipo"] is not None
            c["vizinho"] = v.get("veredito") == "vizinho"
            c["motivo"] = v.get("motivo")
    nossos = nossos_canais()
    for c in canais:
        if c["id"] in nossos:
            c.update(copia=False, tipo=None, vizinho=False, nosso=True, motivo="canal nosso")
    canais.sort(key=lambda c: (-c["copia"], -c["formula"], -c["tema"], -c["views_tema"]))
    copias = [c for c in canais if c["copia"]]
    views = sum(v["views"] for v in videos)
    if copias:
        ver = "Ocupada: já tem cópia do modelo"
    elif views >= 20000:
        ver = "Livre: tem público e ninguém copiou"
    else:
        ver = "Livre, mas pouco sinal de público"
    # vizinho = mesmo assunto, ainda sem a fórmula: não ocupa a língua, mas pode virar cópia (caso do italiano)
    vizinhos = [c for c in canais if c.get("vizinho")]
    return {"buscas": buscas, "videos_tema": len(videos), "views_tema": views, "top": videos[:4],
            "canais": (copias + vizinhos + [c for c in canais if not c["copia"] and not c.get("vizinho")])[:8],
            "copias": len(copias), "vizinhos": len(vizinhos), "veredito": ver}


def termometro(cid, videos, n_titulos=2, dias_max=60):
    # "irmãos" (28/10/2026): pesquisa em inglês os títulos mais vistos do canal e conta quantos canais NOVOS fazem o mesmo.
    # Vários = o formato virou onda em inglês: funciona, e alguém deve levar para as outras línguas logo (correr).
    tops = [v["titulo"] for v in sorted(videos, key=lambda v: -v["views"])[:n_titulos]]
    achados = {}
    for t in tops:
        for v in buscar_cache(t, "en") or []:
            if v.get("canal_id") and v["canal_id"] != cid and v.get("titulo") and parecido(v["titulo"], t) >= 0.4:
                achados.setdefault(v["canal_id"], v)
    with cf.ThreadPoolExecutor(6) as ex:
        feeds = dict(ex.map(triagem, list(achados)))
    novos = []
    for c_id, v in achados.items():
        fd = feeds.get(c_id)
        dias = (HOJE - data(fd["inicio"])).days if fd and fd.get("inicio") else None
        if dias is not None and dias <= dias_max:
            novos.append({"id": c_id, "nome": v["canal"], "dias": dias, "titulo": v["titulo"], "views": v["views"]})
    novos.sort(key=lambda x: -x["views"])
    return {"novos": len(novos), "canais": novos[:6], "dias_max": dias_max}


def analisar_canal(ref, so_linguas=None):
    cid = ref if ref.startswith("UC") else (api("channels", part="id", forHandle=ref if ref.startswith("@") else "@" + ref)
                                            .get("items") or [{}])[0].get("id")
    if not cid:
        print("canal não encontrado:", ref)
        return None
    f = next(fichas_api([cid], {}), None)
    if not f or not f["videos"]:
        print("canal sem vídeos longos:", ref)
        return None
    titulos = [v["titulo"] for v in f["videos"]]
    origem = f["lingua"] or "en"
    temas = temas_do_canal(titulos)
    est_ref = estrutura(titulos)
    nicho_en = temas_do_canal(titulos, n_duplas=0, n_simples=12)   # palavras do nicho, para achar "primos"
    formula_en = formula_do_canal(titulos)                         # palavras do gancho, para achar cópias
    print(f"   fórmula: {formula_en}", flush=True)
    print(f"\n{f['nome']} ({origem}) — temas: {temas}", flush=True)
    alvo = [x for x in (so_linguas or LINGUAS) if x != origem]
    ia = pesquisas_ia(f["nome"], [f"{v['titulo']} ({v['views']:,} views)" for v in f["videos"]], alvo)
    if ia:
        print(f"   nicho (IA): {ia.get('nicho')}", flush=True)

    def uma(l):
        trad = traduzir_lote(temas + nicho_en + formula_en, base(l)) if not ia else [None] * len(temas) + traduzir_lote(nicho_en + formula_en, base(l))
        buscas = (ia["buscas"].get(l) if ia else None) or [b for b in trad[:len(temas)] if b]
        a, b_ = len(temas), len(temas) + len(nicho_en)
        nicho = {raiz(p) for b in trad[a:b_] if b for p in re.findall(r"\w{4,}", sem_acento(b))}
        formula = {raiz(p) for b in trad[b_:] if b for p in re.findall(r"\w{4,}", sem_acento(b))} - nicho
        r = lingua_canal(l, buscas, est_ref, nicho, formula, f["dias"], f["nome"], titulos, cid) if buscas else None
        return l, r or {"buscas": buscas, "veredito": "Erro na pesquisa: rodar de novo", "canais": [], "top": [],
                        "videos_tema": 0, "views_tema": 0, "copias": 0}

    resumo = {}
    with cf.ThreadPoolExecutor(8) as ex:   # as 8 línguas ao mesmo tempo
        resultados = list(ex.map(uma, alvo))
    for l, r in resultados:
        resumo[l] = r
        print(f"  {l}: {resumo[l]['veredito']} | {resumo[l]['videos_tema']} vídeos do tema, "
              f"{resumo[l]['views_tema']:,} views | buscas: {resumo[l]['buscas']}", flush=True)
        for c in resumo[l]["canais"][:4]:
            marca = f"CÓPIA ({c['tipo']}) " if c["copia"] else ""
            print(f"      {marca}{c['nome']} ({c['dias']} dias) — {c.get('motivo') or ''}", flush=True)
    melhor = max(f["videos"], key=lambda v: v["views"])
    try:
        termo = termometro(cid, [v for v in f["videos"] if not v.get("short")] or f["videos"])
        print(f"   termômetro: {termo['novos']} canais novos em inglês fazendo o mesmo", flush=True)
    except Exception as e:
        termo = None
        print("   termômetro falhou:", e, flush=True)
    from cacador import bombando
    bomb, hits = bombando([v["views"] for v in f["videos"] if not v.get("short")], f["dias"])
    return {"tipo": "canal", "tema": f["nome"], "canal": {"id": cid, "nome": f["nome"], "handle": f["handle"],
            "dias": f["dias"], "inscritos": f["inscritos"], "videos": len(f["videos"]),
            "melhor": {"id": melhor["id"], "titulo": melhor["titulo"], "views": melhor["views"]},
            "bomb": bomb, "hits": hits,
            # vídeos longos (do mais novo ao mais velho): o painel mostra para ver a estrutura de título vídeo por vídeo
            "lista": [{"id": v["id"], "t": v["titulo"], "v": v["views"], "d": v.get("duracao"), "p": (v.get("publicado") or "")[:10]}
                      for v in f["videos"] if not v.get("short")][:15]},
            "data": dt.date.today().strftime("%d/%m/%Y"),
            "origem": origem, "temas": temas, "nicho": (ia or {}).get("nicho"), "resumo": resumo, "termometro": termo}


def onda(m):
    # cópias novas (até 45 dias) do modelo em várias línguas = alguém está copiando língua por língua;
    # as línguas que sobram costumam ser ocupadas em semanas
    novas = [(l, c["nome"], c["dias"]) for l, r in m["resumo"].items() for c in r.get("canais", [])
             if c.get("copia") and c.get("dias") is not None and c["dias"] <= 45]
    return {"copias_novas": len(novas), "linguas": sorted({l for l, _, _ in novas}),
            "forte": len({l for l, _, _ in novas}) >= 2}


def juntar(antigo, novo, so=None):
    # junta a análise nova com a anterior: guarda o histórico do canal e anota o que mudou (cópia nova, língua
    # que deixou de estar livre, canal que explodiu). Sem anterior, só começa o histórico.
    hoje = novo["data"]
    if antigo:
        if so:   # refez só algumas línguas: as outras continuam como estavam
            novo["resumo"] = {**antigo["resumo"], **novo["resumo"]}
        novo["primeira"] = antigo.get("primeira") or antigo["data"]
        novo["historico"] = antigo.get("historico") or [{"data": antigo["data"], "inscritos": antigo["canal"].get("inscritos"),
                                                        "videos": antigo["canal"].get("videos"),
                                                        "melhor": (antigo["canal"].get("melhor") or {}).get("views")}]
        # cópia achada antes continua valendo mesmo que a busca de hoje não a traga (a busca do YouTube varia de uma vez
        # para outra: uma língua já "perdeu" 3 cópias numa reanálise)
        nossos = nossos_canais()
        for l, r in novo["resumo"].items():
            a = antigo["resumo"].get(l) or {}
            ids = {c["id"] for c in r.get("canais", [])}
            antes_copia = {c["id"]: c for c in a.get("canais", []) if c.get("copia") and c["id"] not in nossos}
            for c in r.get("canais", []):   # o juiz (IA) às vezes muda de opinião: julgado cópia uma vez, continua cópia
                if c["id"] in antes_copia and not c.get("copia"):
                    v = antes_copia[c["id"]]
                    c.update(copia=True, tipo=v.get("tipo"), motivo=v.get("motivo"), visto_antes=v.get("visto_antes") or antigo.get("data"))
            velhas = [dict(c, visto_antes=c.get("visto_antes") or antigo.get("data")) for c in antes_copia.values()
                      if c["id"] not in ids]
            if velhas:
                r["canais"] = [c for c in r.get("canais", []) if c.get("copia")] + velhas + \
                              [c for c in r.get("canais", []) if not c.get("copia")]
                r["copias"] = sum(1 for c in r["canais"] if c.get("copia"))
                if r["veredito"].startswith("Livre"):
                    r["veredito"] = "Ocupada: já tem cópia do modelo"
        alertas = list(antigo.get("alertas") or [])
        for l, r in novo["resumo"].items():
            a = antigo["resumo"].get(l) or {}
            velhas = {c["id"] for c in a.get("canais", []) if c.get("copia")}
            for c in r.get("canais", []):
                if c.get("copia") and c["id"] not in velhas:
                    alertas.append({"data": hoje, "lingua": l, "texto": f"Cópia nova: {c['nome']}"
                                    + (f" ({c['dias']} dias)" if c.get("dias") is not None else ""), "canal": c["id"]})
            if a.get("veredito", "").startswith("Livre") and r["veredito"].startswith("Ocupada"):
                alertas.append({"data": hoje, "lingua": l, "texto": "Era livre e agora está ocupada"})
        h0 = novo["historico"][-1]
        ins0, ins1 = h0.get("inscritos") or 0, novo["canal"].get("inscritos") or 0
        if ins0 and ins1 >= 2 * ins0 and ins1 - ins0 >= 5000:
            alertas.append({"data": hoje, "lingua": None, "texto": f"O canal explodiu: {ins0:,} → {ins1:,} inscritos".replace(",", ".")})
        novo["alertas"] = [x for i, x in enumerate(alertas) if (x["data"], x["lingua"], x["texto"]) not in
                           {(y["data"], y["lingua"], y["texto"]) for y in alertas[:i]}][-15:]
    else:
        novo["primeira"], novo["historico"], novo["alertas"] = hoje, [], []
    ponto = {"data": hoje, "inscritos": novo["canal"].get("inscritos"), "videos": novo["canal"].get("videos"),
             "melhor": (novo["canal"].get("melhor") or {}).get("views")}
    novo["historico"] = [h for h in novo["historico"] if h["data"] != hoje][-20:] + [ponto]
    novo["onda"] = onda(novo)
    if (novo["canal"].get("videos") or 0) < 3:
        novo["aviso"] = f"Só {novo['canal']['videos']} vídeo(s) longo(s): o modelo ainda não está confirmado. O robô reanalisa sozinho."
    else:
        novo.pop("aviso", None)
    return novo


def guardar(r, so=None):
    todos = json.loads(SAIDA.read_text()) if SAIDA.exists() else []
    antigo = next((m for m in todos if m.get("tipo") == "canal" and m["canal"]["id"] == r["canal"]["id"]), None)
    r = juntar(antigo, r, so)
    try:   # tipo do canal no radar: usado no painel
        from cacador import abrir_banco
        from cacador import oportunidade
        b = abrir_banco()
        t = b.execute("select tipo from canais where id = ?", (r["canal"]["id"],)).fetchone()
        if t and t[0]:
            r["canal"]["tipo"] = t[0]
        pa = b.execute("select producao, adapta from canais where id = ?", (r["canal"]["id"],)).fetchone()
        if pa and pa[0]:
            r["canal"]["prod"], r["canal"]["adapta"] = pa
        r["canal"]["op"] = oportunidade(r["canal"].get("bomb") or 0, r["canal"].get("prod"), r["canal"].get("adapta"))
    except Exception:
        pass
    try:   # línguas em que já estamos (malandro_vigia.json): o painel não sugere essas
        vig = json.loads((PASTA / "malandro_vigia.json").read_text()).get(r["canal"]["id"])
        if vig:
            r["nossas"] = vig["linguas"]
    except Exception:
        pass
    for k in ("nota", "vigia_em", "origem"):   # anotação feita à mão, última vigia e origem (auto) continuam
        if antigo and k in antigo:
            r[k] = antigo[k]
    todos = [r] + [m for m in todos if not (m.get("tipo") == "canal" and m["canal"]["id"] == r["canal"]["id"])]
    SAIDA.write_text(json.dumps(todos, ensure_ascii=False, indent=1))
    return r


def main():
    args = sys.argv[1:]
    todos = json.loads(SAIDA.read_text()) if SAIDA.exists() else []
    so = None
    if "--linguas" in args:   # refaz só estas línguas e junta com a análise que já existe
        i = args.index("--linguas")
        so = args[i + 1].split(",")
        args = args[:i] + args[i + 2:]
    if args and args[0] == "--canal":
        for ref in args[1:]:
            r = analisar_canal(ref.strip(), so)
            if r:
                r = guardar(r, so)
                for a in r.get("alertas", []):
                    if a["data"] == r["data"]:
                        print(f"  ALERTA {a['lingua'] or ''}: {a['texto']}", flush=True)
        return
    pares = [a.split("|", 1) for a in args]
    if not pares or any(len(p) != 2 for p in pares):
        sys.exit('uso: python3 scripts/malandro.py --canal @handle   ou   "título que estourou|assunto"')
    for titulo, chave in pares:
        r = analisar(titulo.strip(), chave.strip())
        todos = [r] + [m for m in todos if m["tema"].lower() != titulo.strip().lower()]
        print(f"\n{titulo}  (assunto: {chave})")
        for x in r["linguas"]:
            print(f"  {x['lingua']}  {x['recentes']:>2} vídeos do assunto no ano ({x['parecidos']} com título parecido), "
                  f"top10 média {x['media_top']:>9,}  {x['veredito']}  ({x['busca']} / {x['chave']})")
    SAIDA.write_text(json.dumps(todos, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
