# Malandro nos canais que o usuário SALVOU na tela do radar 2.0 (só sob comando: nada automático).
# Passe aqui os IDs dos canais (UC...), que a tela mostra em cada canal salvo.
# Uso: python3 scripts/radar2/malandro_salvos.py UC... UC... [--analise 1|2] [--refazer]
#   Análise 1 (padrão): as 8 primeiras línguas do ranking. Análise 2: as outras 6 (só quando o usuário pedir).
#   sem --refazer, pula canal que já tem estas línguas numa análise de menos de 7 dias
import datetime as dt, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import malandro
import radar


# ranking das línguas (falantes × RPM): alemão, espanhol, francês, português, italiano, holandês,
# sueco, polonês | norueguês, dinamarquês, finlandês, romeno, croata, tcheco
ANALISES = {1: ["de", "es", "fr", "pt", "it", "nl", "sv", "pl"], 2: ["no", "da", "fi", "ro", "hr", "cs"]}


def recente(cid, linguas, dias=7):
    try:
        todos = json.loads(malandro.SAIDA.read_text())
    except Exception:
        return False
    m = next((m for m in todos if m.get("tipo") == "canal" and m["canal"]["id"] == cid), None)
    return bool(m) and set(linguas) <= set(m.get("resumo") or {}) and (dt.date.today() - dt.datetime.strptime(m["data"], "%d/%m/%Y").date()).days < dias


def main():
    args = sys.argv[1:]
    refazer = "--refazer" in args
    analise = int(args[args.index("--analise") + 1]) if "--analise" in args else 1
    linguas = ANALISES[analise]
    ids = [a for a in args if a.startswith("UC")]
    salvos = json.loads(radar.SALVOS.read_text()) if radar.SALVOS.exists() else []
    radar.SALVOS.write_text(json.dumps(list(dict.fromkeys(salvos + ids))))
    for cid in ids:
        if not refazer and recente(cid, linguas):
            print(f"{cid}: análise {analise} recente, pulei", flush=True)
            continue
        print(f"{cid}: análise {analise} ({', '.join(linguas)})", flush=True)
        r = malandro.analisar_canal(cid, linguas)
        if r:
            malandro.guardar(r, linguas)
    radar.exportar(radar.conectar())


if __name__ == "__main__":
    main()
