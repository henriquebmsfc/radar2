# Radar 2.0 (29/10/2026): acha canais em inglês com até 21 dias que já estão estourando e ainda não foram copiados.
#   1. BUSCA   palavras amplas de título ("why", "secret", "abandoned"…), só vídeos da ÚLTIMA SEMANA, pela busca grátis
#              (devagar) e pela API oficial (revezando as palavras, dentro da cota)
#   2. IDADE   canal nunca visto: data de criação (ou do 1º vídeo, conta antiga que começou agora) pela API
#   3. FICHA   canais com até 21 dias: vídeos longos, views, inscritos (grátis, yt-dlp)
#   4. FOCO    regras (escrita, Índia, games…) + IA (Haiku) nos que passaram: tira drama, notícia, famosos, vlog…
#   5. BOLA DE NEVE  recomendados ao lado dos vídeos que estouraram nos canais bons → cópias e irmãos (repete 2–4)
#   6. EXPORTA estudos/radar2/dados.json (a tela)
# Todo par canal×pesquisa fica guardado (tabela achados): cada palavra é julgada pelos canais BONS que trouxe.
# Uso: python3 scripts/radar2/radar.py [--api N] [--sem-gratis] [--max-paginas N]
import argparse, concurrent.futures as cf, datetime as dt, glob, json, random, re, shutil, sqlite3, statistics as st, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PASTA = RAIZ / "estudos" / "radar2"
BANCO = PASTA / "radar2.sqlite"
HOJE = dt.datetime.now(dt.timezone.utc)
DIA = HOJE.date().isoformat()
MAX_DIAS = 21
SEMANA = "CAMSBAgDEAE%3D"           # busca grátis: mais vistos + esta semana + só vídeos
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# palavras amplas: pegam qualquer nicho, sem depender de uma lista de assuntos
PALAVRAS = [
    "why", "how", "what if", "what happens", "secret", "hidden", "truth", "nobody", "mystery", "forgotten", "strange",
    "inside", "lost", "abandoned", "real story", "explained", "documentary", "history", "never", "biggest", "worst",
    "entire", "everything", "finally", "shocking", "terrifying", "insane", "impossible", "dangerous", "deadliest",
    "ancient", "empire", "collapse", "disaster", "banned", "illegal", "richest", "poorest", "last", "first time",
    "the dark", "the rise", "the fall", "day by day", "hour by hour", "on a map", "ranked", "every", "until",
    "the truth about", "the real reason", "how did", "why did", "why does", "why is", "what really", "what it was like",
    "life in", "they found", "scientists", "archaeologists", "engineers", "military", "war", "battle", "army", "navy",
    "pilot", "prison", "island", "ocean", "desert", "mountain", "city", "country", "village", "kingdom", "king",
    "emperor", "billion", "economy", "crash", "mafia", "cartel", "spy", "murder", "vanished", "survived", "discovered",
    "built", "destroyed", "nuclear", "space", "planet", "earth", "animal", "predator", "machine", "invention", "company",
]


def conectar():
    PASTA.mkdir(parents=True, exist_ok=True)
    b = sqlite3.connect(BANCO)
    b.executescript("""
    create table if not exists canais(id text primary key, nome text, inicio text, idade_fonte text, visto_em text,
        ficha text, ficha_em text, classe text, foco text, foco_motivo text, tipo text);
    create table if not exists achados(canal text, dia text, metodo text, caminho text, termo text,
        primary key(canal, dia, metodo, caminho, termo));
    create table if not exists fotos(canal text, dia text, inscritos int, views int, n_longos int, primary key(canal, dia));
    """)
    return b


# ---------- API oficial ----------
class CotaAcabou(Exception):
    pass


def chave():
    env = RAIZ / ".env"
    for l in env.read_text().splitlines() if env.exists() else []:
        if l.startswith("YOUTUBE_API_KEY="):
            k = l.split("=", 1)[1].strip().strip('"')
            if k:
                return k
    sys.exit("Falta a chave do YouTube: copie .env.exemplo para .env e preencha YOUTUBE_API_KEY (ver README.md).")


def claude_cli():
    # o Claude Code instalado no computador ou o que vem na extensão do VS Code; usa o login (assinatura), sem chave de API
    achado = shutil.which("claude")
    if achado:
        return achado
    achados = sorted(glob.glob(str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")) +
                     glob.glob(str(Path.home() / ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude")),
                     key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("claude-code-")[1].split("/")[0])])
    if achados:
        return achados[-1]
    sys.exit("Não achei o Claude Code: ele é usado para filtrar os canais pelo assunto (ver README.md).")


PONTOS = {"gastos": 0, "acabou": False}


def api(recurso, custo, **p):
    if PONTOS["acabou"]:
        raise CotaAcabou()
    u = f"https://www.googleapis.com/youtube/v3/{recurso}?" + urllib.parse.urlencode({**p, "key": chave()})
    for tentativa in range(3):
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=30).read())
            PONTOS["gastos"] += custo
            return r
        except urllib.error.HTTPError as e:
            corpo = e.read().decode("utf-8", "ignore")
            # 429 "Search Queries per day": limite só de PESQUISAS (o resto da API continua funcionando)
            if e.code == 429 or "Quota exceeded" in corpo:
                if recurso == "search":
                    raise CotaAcabou()
                PONTOS["acabou"] = True
                raise CotaAcabou()
            if "quotaExceeded" in corpo:
                PONTOS["acabou"] = True
                raise CotaAcabou()
            if e.code in (400, 403, 404):
                return {}
        except Exception:
            pass
        time.sleep(3 + 4 * tentativa)
    return {}


def busca_api(termo):
    # vídeos de 4 min ou mais da última semana, mais vistos (médios + longos = 200 pontos)
    depois = (HOJE - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    achados = set()
    for dur in ("medium", "long"):
        r = api("search", 100, part="snippet", q=termo, type="video", order="viewCount", publishedAfter=depois,
                relevanceLanguage="en", videoDuration=dur, maxResults=50)
        achados |= {it["snippet"]["channelId"] for it in r.get("items", [])}
    return achados


# ---------- busca grátis ----------
def ytdlp(url, fim):
    for tentativa in range(2):
        try:
            o = subprocess.run(["yt-dlp", "--js-runtimes", "node", "--extractor-args", "youtube:lang=en", "--flat-playlist",
                                "-J", "--playlist-end", str(fim), url], capture_output=True, text=True, timeout=240)
            if o.returncode == 0 and o.stdout:
                return json.loads(o.stdout)
        except Exception:
            pass
        time.sleep(5 + 10 * tentativa)
    return None


def busca_gratis(termo):
    time.sleep(random.uniform(3, 7))   # devagar: o YouTube segura buscas em rajada
    d = ytdlp("https://www.youtube.com/results?search_query=" + urllib.parse.quote(termo) + "&sp=" + SEMANA, 120)
    if d is None:
        return None
    return {e["channel_id"] for e in d.get("entries") or []
            if e.get("channel_id") and (e.get("duration") or 0) >= 240 and (e.get("view_count") or 0) >= 2000}


def recomendados(video_id):
    time.sleep(random.uniform(3, 7))
    req = urllib.request.Request("https://www.youtube.com/watch?v=" + video_id, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    try:
        h = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        j = re.search(r"var ytInitialData = (\{.*?\});</script>", h).group(1)
    except Exception:
        return None
    return set(re.findall(r'"browseId":"(UC[\w-]{22})"', j)) | set(re.findall(r'"browseId": "(UC[\w-]{22})"', j))


# ---------- idade e ficha ----------
def idades(b, cids):
    falta = [c for c in cids if not b.execute("select 1 from canais where id=? and inicio is not null", (c,)).fetchone()]
    try:
        for i in range(0, len(falta), 50):
            r = api("channels", 1, part="snippet,statistics,contentDetails", id=",".join(falta[i:i + 50]), maxResults=50)
            for c in r.get("items", []):
                inicio = c["snippet"].get("publishedAt")
                n = int(c.get("statistics", {}).get("videoCount", 0) or 0)
                if 0 < n <= 15:   # conta antiga que só começou a postar agora: vale a data do 1º vídeo
                    up = c["contentDetails"]["relatedPlaylists"]["uploads"]
                    its = api("playlistItems", 1, part="contentDetails", playlistId=up, maxResults=15).get("items", [])
                    datas = [x["contentDetails"].get("videoPublishedAt") for x in its if x["contentDetails"].get("videoPublishedAt")]
                    if datas and min(datas) > (inicio or ""):
                        inicio = min(datas)
                b.execute("insert into canais(id, nome, inicio, idade_fonte, visto_em) values(?,?,?,'api',?) on conflict(id) do "
                          "update set nome=excluded.nome, inicio=excluded.inicio, idade_fonte='api'",
                          (c["id"], c["snippet"]["title"], inicio, DIA))
            b.commit()
    except CotaAcabou:
        print("   cota da API acabou na idade: o resto fica para amanhã", flush=True)
    return len(falta)


def novos(b, cids=None):
    corte = (HOJE - dt.timedelta(days=MAX_DIAS)).isoformat()
    q = "select id from canais where inicio >= ?"
    ids = [r[0] for r in b.execute(q, (corte,))]
    return [c for c in ids if cids is None or c in cids]


def ficha(cid):
    d = ytdlp(f"https://www.youtube.com/channel/{cid}/videos", 60)
    if d is None:
        return None
    longos = [e for e in d.get("entries") or [] if (e.get("duration") or 0) >= 240]
    vs = [e.get("view_count") or 0 for e in longos]
    return {"n_longos": len(longos), "mediana": int(st.median(vs)) if vs else 0, "hits10k": sum(v >= 10000 for v in vs),
            "maior": max(vs) if vs else 0, "views": sum(vs), "inscritos": d.get("channel_follower_count"),
            "nome": d.get("channel") or d.get("title"), "handle": d.get("uploader_id"),
            "desc": (d.get("description") or "")[:300],
            "videos": [{"id": e.get("id"), "t": e.get("title"), "v": e.get("view_count") or 0, "d": e.get("duration")}
                       for e in sorted(longos, key=lambda e: -(e.get("view_count") or 0))[:12]]}


def classe(f):
    if f["mediana"] >= 20000 and f["hits10k"] >= 2:
        return "bom"
    if f["maior"] >= 50000 or (f["n_longos"] <= 3 and f["maior"] >= 20000):
        return "promissor"
    return "fraco"


def fichar(b, cids):
    cids = [c for c in cids if not b.execute("select 1 from canais where id=? and ficha_em=?", (c, DIA)).fetchone()]
    with cf.ThreadPoolExecutor(3) as ex:
        for cid, f in zip(cids, ex.map(ficha, cids)):
            if f is None:
                continue
            b.execute("update canais set ficha=?, ficha_em=?, classe=?, nome=coalesce(?, nome) where id=?",
                      (json.dumps(f, ensure_ascii=False), DIA, classe(f), f["nome"], cid))
            b.execute("insert or replace into fotos values(?,?,?,?,?)", (cid, DIA, f["inscritos"], f["views"], f["n_longos"]))
            b.commit()
    return len(cids)


# ---------- foco ----------
ESCRITA_FORA = re.compile(r"[؀-ۿऀ-෿฀-๿ក-៿぀-ヿ一-鿿가-힯]")
PALAVRAS_FORA = re.compile(r"survival|bushcraft|primitive|\bgta\b|minecraft|roblox|fortnite|gameplay|\bgaming\b|\bkids?\b|"
                           r"cartoon|nursery|\btoys?\b|\bhindi\b|\burdu\b|bollywood|\bipl\b|cricket|\bindia\b|\bindian\b|"
                           r"\bmodi\b|\bdesi\b|tamil|telugu|punjabi|pakistan|vlog|prank|challenge|asmr|\bfull movie\b|"
                           r"complete movie|\bepisode \d|\bep\.? ?\d", re.I)
TIPOS_FORA = ["drama ou ficção narrada", "notícia ou política do dia", "famosos atuais", "vlog ou rosto/filmagem própria",
              "games", "infantil", "música", "filme ou série repostada", "Índia/Sul da Ásia/Vietnã", "guru ou especialista"]


def regras(f):
    tits = [v["t"] or "" for v in f["videos"]] + [f.get("nome") or ""]
    if sum(bool(ESCRITA_FORA.search(t)) for t in tits) >= max(1, len(tits) // 4):
        return "escrita de fora"
    if sum(bool(PALAVRAS_FORA.search(t)) for t in tits) >= max(1, len(tits) // 4):
        return "palavras de fora"
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0
    try:
        if detect(" . ".join(tits[:12])) != "en":
            return "não é inglês"
    except Exception:
        pass
    return None


def focar(b):
    # só quem é bom ou promissor e ainda não foi julgado
    ids = [r for r in b.execute("select id, ficha from canais where classe in ('bom','promissor') and foco is null and ficha is not null")]
    pra_ia = []
    for cid, fx in ids:
        m = regras(json.loads(fx))
        if m:
            b.execute("update canais set foco='fora', foco_motivo=? where id=?", (m, cid))
        else:
            pra_ia.append((cid, json.loads(fx)))
    b.commit()
    for i in range(0, len(pra_ia), 30):
        lote = pra_ia[i:i + 30]
        txt = "\n".join(f"{k}. {f.get('nome')}: " + " | ".join((v['t'] or '')[:90] for v in f['videos'][:5]) for k, (_, f) in enumerate(lote))
        prompt = f"""Classifique cada canal do YouTube pelos títulos. Queremos canais de vídeo longo NARRADO com material real
(documentário, explicação, história, ciência, "e se", mistério, geografia, economia, crime real, engenharia, natureza).
Fora do foco: {"; ".join(TIPOS_FORA)}.
Para cada canal responda o tipo de conteúdo em 2 a 4 palavras em português e se está "ok" ou qual item de fora.
Canais:
{txt}
Responda SÓ com JSON: [{{"n": 0, "tipo": "...", "foco": "ok" ou "<item de fora>"}}, ...]"""
        try:
            o = subprocess.run([claude_cli(), "-p", prompt, "--model", "haiku", "--output-format", "text"],
                               capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            r = json.loads(o.stdout[o.stdout.find("["):o.stdout.rfind("]") + 1])
        except Exception as e:
            print("   IA do foco falhou:", e, flush=True)
            continue
        for x in r:
            if isinstance(x.get("n"), int) and x["n"] < len(lote):
                ok = str(x.get("foco", "")).strip().lower() == "ok"
                b.execute("update canais set foco=?, foco_motivo=?, tipo=? where id=?",
                          ("ok" if ok else "fora", None if ok else x.get("foco"), x.get("tipo"), lote[x["n"]][0]))
        b.commit()


# ---------- rodada ----------
def guardar(b, metodo, caminho, termo, cids):
    b.executemany("insert or ignore into achados values(?,?,?,?,?)", [(c, DIA, metodo, caminho, termo) for c in cids])
    b.executemany("insert or ignore into canais(id, visto_em) values(?,?)", [(c, DIA) for c in cids])
    b.commit()


def passo_novos(b, cids, rotulo):
    print(f"   {rotulo}: {len(cids)} canais; idade de {idades(b, cids)} nunca vistos", flush=True)
    nv = novos(b, set(cids))
    print(f"   {len(nv)} com até {MAX_DIAS} dias → fichas: {fichar(b, nv)}", flush=True)
    focar(b)


def bons_no_foco(b):
    corte = (HOJE - dt.timedelta(days=MAX_DIAS)).isoformat()
    return [(r[0], json.loads(r[1])) for r in b.execute(
        "select id, ficha from canais where inicio >= ? and foco='ok' and classe in ('bom','promissor') order by classe", (corte,))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", type=int, default=35, help="palavras pesquisadas pela API (200 pontos cada)")
    ap.add_argument("--sem-gratis", action="store_true")
    ap.add_argument("--max-paginas", type=int, default=60, help="páginas de vídeo lidas na bola de neve")
    a = ap.parse_args()
    b = conectar()
    inicio = time.time()

    # 1. BUSCA: a API gira pela lista (cada dia começa de onde parou); a grátis passa por todas as palavras
    achados = set()
    giro = int(json.loads((PASTA / "giro.json").read_text())["proxima"]) if (PASTA / "giro.json").exists() else 0
    lista_api = [PALAVRAS[(giro + i) % len(PALAVRAS)] for i in range(min(a.api, len(PALAVRAS)))]
    print(f"1. busca: {len(lista_api)} palavras pela API, {0 if a.sem_gratis else len(PALAVRAS)} pela grátis", flush=True)
    feitas = 0
    for t in lista_api:
        try:
            r = busca_api(t)
        except CotaAcabou:
            print(f"   cota da API acabou depois de {feitas} palavras", flush=True)
            break
        guardar(b, "palavras", "api", t, r)
        achados |= r
        feitas += 1
    (PASTA / "giro.json").write_text(json.dumps({"proxima": (giro + feitas) % len(PALAVRAS)}))
    if not a.sem_gratis:
        vazias = 0
        with cf.ThreadPoolExecutor(2) as ex:
            for t, r in zip(PALAVRAS, ex.map(busca_gratis, PALAVRAS)):
                if r is None:
                    vazias += 1
                    if vazias >= 8:
                        print("   o YouTube está segurando a busca grátis; paro aqui", flush=True)
                        ex.shutdown(cancel_futures=True)
                        break
                    continue
                vazias = 0
                guardar(b, "palavras", "grátis", t, r)
                achados |= r
    passo_novos(b, achados, "palavras")

    # 2. BOLA DE NEVE: recomendados dos 2 vídeos mais vistos de cada canal bom (até 3 voltas ou o limite de páginas)
    lidos, paginas = set(), 0
    for volta in range(3):
        sementes = [(cid, v["id"]) for cid, f in bons_no_foco(b) if cid not in lidos for v in f["videos"][:2]]
        sementes = sementes[:max(0, a.max_paginas - paginas)]
        if not sementes:
            break
        print(f"2. bola de neve, volta {volta + 1}: {len(sementes)} vídeos", flush=True)
        lidos |= {c for c, _ in sementes}
        novos_r = set()
        with cf.ThreadPoolExecutor(2) as ex:
            for (cid, vid), r in zip(sementes, ex.map(lambda s: recomendados(s[1]), sementes)):
                paginas += 1
                if r:
                    r.discard(cid)
                    guardar(b, "recomendados", "página", cid, r)
                    novos_r |= r
        passo_novos(b, novos_r, f"recomendados (volta {volta + 1})")

    # 3. EXPORTA
    exportar(b)
    print(f"pronto em {round((time.time() - inicio) / 60)} min; API: {PONTOS['gastos']} pontos", flush=True)


SALVOS = PASTA / "salvos.json"   # canais que o usuário salvou na tela (gravado ao rodar o Malandro neles)


def linguas_malandro():
    # resultado do Malandro por canal: {id: {"data": ..., "linguas": {l: {"estado": livre|fraca|ocupada|erro, "copias": n}}}}
    try:
        todos = json.loads((RAIZ / "estudos/cacador/painel/malandro.json").read_text())
    except Exception:
        return {}
    out = {}
    for m in todos:
        if m.get("tipo") != "canal":
            continue
        ls = {}
        for l, r in (m.get("resumo") or {}).items():
            v = r.get("veredito") or ""
            estado = ("livre" if v.startswith("Livre: tem") else "fraca" if v.startswith("Livre") else
                      "ocupada" if v.startswith("Ocupada") else "erro")
            ls[l] = {"estado": estado, "copias": r.get("copias", 0), "views": r.get("views_tema", 0)}
        out[m["canal"]["id"]] = {"data": m.get("data"), "linguas": ls, "nossas": m.get("nossas") or []}
    return out


def exportar(b):
    nomes = dict(b.execute("select id, nome from canais where nome is not null"))
    mal = linguas_malandro()
    salvos = json.loads(SALVOS.read_text()) if SALVOS.exists() else []
    lista = bons_no_foco(b)
    ja = {c for c, _ in lista}
    # salvos continuam na tela mesmo depois de passar de 21 dias
    lista += [(c, json.loads(r[0])) for c in salvos if c not in ja
              for r in [b.execute("select ficha from canais where id=? and ficha is not null", (c,)).fetchone()] if r]
    out = []
    for cid, f in lista:
        ini, tipo, cl = b.execute("select inicio, tipo, classe from canais where id=?", (cid,)).fetchone()
        quem = b.execute("select metodo, caminho, termo, min(dia) from achados where canal=? group by metodo, caminho, termo", (cid,)).fetchall()
        # irmãos: canais novos que apareceram nos recomendados dos vídeos DESTE canal (cópias, mesmo formato)
        irmaos = [r[0] for r in b.execute("select distinct a.canal from achados a join canais c on c.id=a.canal where a.metodo='recomendados' "
                                          "and a.termo=? and c.inicio >= ? and c.classe in ('bom','promissor') and c.foco='ok'",
                                          (cid, (HOJE - dt.timedelta(days=60)).isoformat()))]
        conferido = bool(b.execute("select 1 from achados where metodo='recomendados' and termo=? limit 1", (cid,)).fetchone())
        fotos = b.execute("select dia, inscritos, views from fotos where canal=? order by dia", (cid,)).fetchall()
        out.append({"id": cid, "nome": f.get("nome") or nomes.get(cid), "handle": f.get("handle"), "tipo": tipo, "classe": cl,
                    "dias": max(1, (HOJE - dt.datetime.fromisoformat(ini.replace("Z", "+00:00"))).days),
                    "inscritos": f.get("inscritos"), "mediana": f["mediana"], "maior": f["maior"], "n_longos": f["n_longos"],
                    "hits10k": f["hits10k"], "desc": f.get("desc"), "videos": f["videos"][:6],
                    "achado_por": [{"metodo": m, "caminho": c, "termo": nomes.get(t, t), "dia": d} for m, c, t, d in quem],
                    "irmaos": [{"id": i, "nome": nomes.get(i)} for i in irmaos], "copias_conferidas": conferido,
                    "malandro": mal.get(cid),
                    "historico": fotos})
    out.sort(key=lambda x: (x["classe"] != "bom", -x["mediana"]))
    dados = json.dumps({"gerado": HOJE.isoformat(timespec="minutes"), "canais": out}, ensure_ascii=False)
    (PASTA / "dados.json").write_text(dados)
    # a tela (estudos/radar2/tela): dados + miniatura do vídeo que mais estourou de cada canal, embutida no arquivo
    # (a página não carrega imagem de fora); as baixadas ficam guardadas em miniaturas/ para não baixar de novo
    import base64, io
    from PIL import Image
    tela = PASTA / "tela"
    (tela / "miniaturas").mkdir(parents=True, exist_ok=True)
    for x in out:
        vid = x["videos"][0]["id"] if x["videos"] else None
        arq = tela / "miniaturas" / f"{vid}.jpg"
        if vid and not arq.exists():
            try:
                img = Image.open(io.BytesIO(urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", timeout=20).read()))
                img.convert("RGB").resize((256, 144)).save(arq, "JPEG", quality=70)
            except Exception:
                pass
        if vid and arq.exists():
            x["mini"] = "data:image/jpeg;base64," + base64.b64encode(arq.read_bytes()).decode()
    (tela / "dados.json").write_text(json.dumps({"gerado": HOJE.isoformat(timespec="minutes"), "canais": out}, ensure_ascii=False))
    vivos = {f"{x['videos'][0]['id']}.jpg" for x in out if x["videos"]}
    for arq in (tela / "miniaturas").glob("*.jpg"):
        if arq.name not in vivos:
            arq.unlink()
    print(f"3. exportado: {len(out)} oportunidades ({sum(x['classe'] == 'bom' for x in out)} boas)", flush=True)


if __name__ == "__main__":
    main()
