import argparse
import csv
import ipaddress
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CAMPOS_SAIDA = ["ano", "URL", "URL_não_normalizada"]
CAMPOS_REJEITADAS = ["arquivo", "linha", "motivo", "date", "URL"]
ANO_MINIMO = 2019
ANO_MAXIMO = 2025


def _encontrar_arquivos(diretorio: Path) -> list[Path]:
    """Encontra CSVs mensais nomeados como YYYYMM.csv (ex: 202501.csv)."""
    arquivos = []
    for arquivo in diretorio.glob("*.csv"):
        nome = arquivo.stem
        if (
            len(nome) == 6
            and nome.isdigit()
            and 1 <= int(nome[4:6]) <= 12
        ):
            arquivos.append(arquivo)
    return sorted(arquivos)


def _parsear_data(data_texto: str) -> datetime:
    """Interpreta a data da coluna 'date'.

    O ano vem sempre da própria data confirmada em cada linha, nunca do
    nome do arquivo mensal. Isso importa porque um phishing pode ser
    reportado num arquivo (ex: 202501.csv) mas ter sido confirmado em
    31/12 do ano anterior, ou vice-versa - usar o nome do arquivo geraria
    um ano errado nessas bordas de virada de mês/ano.
    """
    valor = data_texto.strip()
    if not valor:
        raise ValueError("data vazia")

    valor_iso = valor.replace("/", "-")
    try:
        parsed = datetime.fromisoformat(valor_iso)
    except ValueError:
        try:
            parsed = datetime.combine(
                date.fromisoformat(valor_iso[:10]), datetime.min.time()
            )
        except ValueError as erro:
            raise ValueError(f"data inválida: {valor}") from erro
    return parsed.replace(tzinfo=None)


def _normalizar_url(url: str) -> tuple[str, str]:
    """Valida a URL e retorna (url_normalizada, chave_de_deduplicacao).

    A URL normalizada vai para a coluna URL; a versão original permanece
    em URL_não_normalizada. O caminho, a query e o fragmento são preservados.

    A chave de deduplicação descarta o fragmento (#...), que normalmente
    não muda o conteúdo servido pelo host e só infla contagem de URLs
    "únicas" que na prática são a mesma página.
    """
    if any(caractere.isspace() for caractere in url):
        raise ValueError("URL contém espaços ou caracteres de whitespace")

    try:
        partes = urlsplit(url)
        scheme = partes.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("esquema diferente de HTTP/HTTPS")

        host = partes.hostname
        if not host:
            raise ValueError("hostname ausente")
        port = partes.port
    except ValueError as erro:
        raise ValueError(f"URL inválida: {erro}") from erro

    host = host.rstrip(".").lower()
    if not host:
        raise ValueError("hostname vazio")

    try:
        endereco_ip = ipaddress.ip_address(host)
        host_normalizado = f"[{host}]" if endereco_ip.version == 6 else host
    except ValueError:
        try:
            host_normalizado = host.encode("idna").decode("ascii")
        except UnicodeError as erro:
            raise ValueError(f"hostname inválido: {erro}") from erro

    userinfo = partes.netloc.rpartition("@")
    prefixo_usuario = f"{userinfo[0]}@" if userinfo[1] else ""
    porta_padrao = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    sufixo_porta = f":{port}" if port is not None and not porta_padrao else ""
    netloc = f"{prefixo_usuario}{host_normalizado}{sufixo_porta}"

    url_normalizada = urlunsplit(
        (scheme, netloc, partes.path or "/", partes.query, partes.fragment)
    )
    chave_deduplicacao = urlunsplit(
        (scheme, netloc, partes.path or "/", partes.query, "")
    )
    return url_normalizada, chave_deduplicacao


def gerar_dataset(
    diretorio_entrada: str | Path,
    diretorio_saida: str | Path,
    escopo_deduplicacao: str = "ano",
) -> tuple[int, int, int, int]:
    if escopo_deduplicacao not in {"global", "ano", "nenhuma"}:
        raise ValueError(
            "escopo_deduplicacao deve ser 'global', 'ano' ou 'nenhuma'"
        )

    diretorio_entrada = Path(diretorio_entrada).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()
    arquivos = _encontrar_arquivos(diretorio_entrada)

    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum CSV mensal com nome YYYYMM foi encontrado em {diretorio_entrada}"
        )

    caminhos_entrada = {arquivo.resolve() for arquivo in arquivos}
    caminho_rejeitadas = diretorio_saida / "jpcert_rejeitadas.csv"
    if caminho_rejeitadas.resolve() in caminhos_entrada:
        raise ValueError("O relatório de rejeições não pode sobrescrever um arquivo de entrada")

    registros_por_ano: dict[str, list[dict[str, str]]] = {}
    candidatos: list[tuple[datetime, str, str, str, str]] = []
    rejeitadas: list[dict[str, str | int]] = []
    registros_validos = 0
    duplicatas = 0
    arquivos_invalidos = 0
    chaves_vistas: set[tuple[str, ...]] = set()

    for arquivo in arquivos:
        print(f"Processando {arquivo.name}...")
        registros_arquivo: list[tuple[datetime, str, str, str, str]] = []
        rejeitadas_arquivo: list[dict[str, str | int]] = []

        try:
            with arquivo.open("r", newline="", encoding="utf-8-sig") as entrada:
                leitor = csv.DictReader(entrada, strict=True)
                nomes_colunas = {
                    (nome or "").strip().casefold(): nome
                    for nome in (leitor.fieldnames or [])
                }
                if "url" not in nomes_colunas or "date" not in nomes_colunas:
                    raise ValueError("cabeçalho precisa conter as colunas 'date' e 'URL'")

                coluna_url = nomes_colunas["url"]
                coluna_data = nomes_colunas["date"]
                for registro in leitor:
                    numero_linha = leitor.line_num
                    url = (registro.get(coluna_url) or "").strip()
                    data_texto = (registro.get(coluna_data) or "").strip()
                    motivo = ""
                    ano = ""
                    data_registro: datetime | None = None
                    url_normalizada = ""
                    chave_deduplicacao = ""

                    if None in registro:
                        motivo = "registro CSV contém campos extras"
                    elif not url:
                        motivo = "URL vazia"
                    else:
                        try:
                            data_registro = _parsear_data(data_texto)
                            ano = str(data_registro.year)
                            if not ANO_MINIMO <= int(ano) <= ANO_MAXIMO:
                                raise ValueError(
                                    f"ano fora do intervalo esperado "
                                    f"{ANO_MINIMO}-{ANO_MAXIMO}: {ano}"
                                )
                            url_normalizada, chave_deduplicacao = _normalizar_url(url)
                        except ValueError as erro:
                            motivo = str(erro)

                    if motivo:
                        rejeitadas_arquivo.append(
                            {
                                "arquivo": arquivo.name,
                                "linha": numero_linha,
                                "motivo": motivo,
                                "date": data_texto,
                                "URL": url,
                            }
                        )
                        continue

                    registros_arquivo.append(
                        (
                            data_registro,
                            ano,
                            url_normalizada,
                            url,
                            chave_deduplicacao,
                        )
                    )
        except (OSError, UnicodeError, csv.Error, ValueError) as erro:
            arquivos_invalidos += 1
            rejeitadas.append(
                {
                    "arquivo": arquivo.name,
                    "linha": "",
                    "motivo": f"arquivo não processado: {erro}",
                    "date": "",
                    "URL": "",
                }
            )
            print(f"AVISO: {arquivo.name} não foi processado: {erro}")
            continue

        rejeitadas.extend(rejeitadas_arquivo)
        registros_validos += len(registros_arquivo)
        candidatos.extend(registros_arquivo)

    if escopo_deduplicacao == "global":
        candidatos.sort(key=lambda registro: registro[0])

    for _, ano, url_normalizada, url_original, chave_deduplicacao in candidatos:
        if escopo_deduplicacao == "global":
            chave = (chave_deduplicacao,)
        elif escopo_deduplicacao == "ano":
            chave = (ano, chave_deduplicacao)
        else:
            chave = ()

        if chave and chave in chaves_vistas:
            duplicatas += 1
            continue
        if chave:
            chaves_vistas.add(chave)

        registros_por_ano.setdefault(ano, []).append(
            {
                "ano": ano,
                "URL": url_normalizada,
                "URL_não_normalizada": url_original,
            }
        )

    diretorio_saida.mkdir(parents=True, exist_ok=True)
    arquivos_esperados: set[Path] = set()
    for ano, registros in sorted(registros_por_ano.items()):
        pasta_ano = diretorio_saida / ano
        pasta_ano.mkdir(parents=True, exist_ok=True)
        arquivo_saida = pasta_ano / f"dataset_phishing_{ano}.csv"
        arquivos_esperados.add(arquivo_saida.resolve())
        with arquivo_saida.open("w", newline="", encoding="utf-8") as saida:
            escritor = csv.DictWriter(saida, fieldnames=CAMPOS_SAIDA)
            escritor.writeheader()
            escritor.writerows(registros)
        print(f"{ano}: {len(registros)} URLs -> {arquivo_saida}")

    with caminho_rejeitadas.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS_REJEITADAS)
        escritor.writeheader()
        escritor.writerows(rejeitadas)

    arquivos_anuais_existentes = diretorio_saida.glob("*/dataset_phishing_*.csv")
    arquivos_obsoletos = sorted(
        (
            arquivo
            for arquivo in arquivos_anuais_existentes
            if arquivo.resolve() not in arquivos_esperados
        ),
        key=lambda arquivo: str(arquivo),
    )
    if arquivos_obsoletos:
        print(
            "AVISO: os seguintes CSVs anuais não foram atualizados e foram mantidos:"
        )
        for arquivo in arquivos_obsoletos:
            print(f"  {arquivo}")

    print(f"URLs válidas antes da deduplicação: {registros_validos}")
    print(f"Duplicatas removidas: {duplicatas}")
    print(f"Registros escritos: {registros_validos - duplicatas}")
    print(f"Registros/arquivos reportados como inválidos: {len(rejeitadas)}")
    print(f"Arquivos mensais não processados: {arquivos_invalidos}")
    print(f"Relatório de rejeições: {caminho_rejeitadas}")
    return registros_validos, duplicatas, len(rejeitadas), arquivos_invalidos


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parents[1]
    diretorio_jpcert = raiz_projeto / "JPCERT"
    parser = argparse.ArgumentParser(
        description=(
            "Organiza URLs de phishing JPCERT por ano da coluna date "
            "e grava CSVs com URLs normalizadas e originais."
        )
    )
    parser.add_argument(
        "--entrada",
        type=Path,
        default=diretorio_jpcert,
        help="Pasta com os CSVs mensais JPCERT nomeados como YYYYMM.csv.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=diretorio_jpcert / "dataset_phishing",
        help="Pasta de saída; cria uma subpasta e um CSV para cada ano.",
    )
    parser.add_argument(
        "--deduplicacao",
        choices=("global", "ano", "nenhuma"),
        default="ano",
        help=(
            "Deduplica URLs normalizadas sem considerar fragmentos. "
            "'ano' (padrão) deduplica dentro de cada ano; 'global' mantém a "
            "ocorrência de data mais antiga no conjunto todo; 'nenhuma' "
            "mantém todas."
        ),
    )
    argumentos = parser.parse_args()
    gerar_dataset(
        argumentos.entrada,
        argumentos.saida,
        escopo_deduplicacao=argumentos.deduplicacao,
    )


if __name__ == "__main__":
    main()