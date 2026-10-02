import argparse
import csv
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CRAWL_POR_ANO = {
    "2019": "CC-MAIN-2019-51",
    "2020": "CC-MAIN-2020-50",
    "2021": "CC-MAIN-2021-49",
    "2022": "CC-MAIN-2022-49",
    "2023": "CC-MAIN-2023-50",
    "2024": "CC-MAIN-2024-51",
    "2025": "CC-MAIN-2025-51",
}

CAMPOS_SAIDA = ["ano", "URL", "URL_não_normalizada"]

CAMPOS_REJEITADAS = ["arquivo", "linha", "motivo", "url", "dominio"]


def _normalizar_url(url: str) -> tuple[str, str, bool]:
    """Valida a URL e retorna sua forma canônica, hostname e flag de IP."""
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

        # Acessar .port também valida que a porta, se informada, é numérica
        # e está dentro do intervalo permitido.
        port = partes.port
    except ValueError as erro:
        raise ValueError(f"URL inválida: {erro}") from erro

    host = host.rstrip(".").lower()
    if not host:
        raise ValueError("hostname vazio")

    try:
        endereco_ip = ipaddress.ip_address(host)
        is_ip_host = True
        host_normalizado = f"[{host}]" if endereco_ip.version == 6 else host
    except ValueError:
        is_ip_host = False
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

    # A ordem da query é preservada: parâmetros repetidos e URLs assinadas
    # podem depender da ordem original.
    url_normalizada = urlunsplit(
        (scheme, netloc, partes.path or "/", partes.query, partes.fragment)
    )
    return url_normalizada, host_normalizado, is_ip_host


def _normalizar_dominio(dominio: str) -> str:
    if any(caractere.isspace() for caractere in dominio) or any(
        caractere in dominio for caractere in "/:@?#"
    ):
        raise ValueError("domínio contém caracteres inválidos")

    dominio = dominio.rstrip(".").lower()
    if not dominio:
        raise ValueError("domínio vazio")

    try:
        return dominio.encode("idna").decode("ascii")
    except UnicodeError as erro:
        raise ValueError(f"domínio inválido: {erro}") from erro


def _hostname_pertence_ao_dominio(host: str, dominio: str) -> bool:
    return host == dominio or host.endswith(f".{dominio}")


def _encontrar_arquivos(diretorio_entrada: Path) -> list[Path]:
    arquivos = sorted(
        diretorio_entrada.glob("*/dataset_legitimo_*.csv"),
        key=lambda arquivo: (arquivo.parent.name, arquivo.name),
    )
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum arquivo */dataset_legitimo_*.csv encontrado em "
            f"{diretorio_entrada}"
        )

    for arquivo in arquivos:
        ano = arquivo.parent.name
        if ano not in CRAWL_POR_ANO:
            raise ValueError(
                f"Ano sem crawl configurado em CRAWL_POR_ANO: {ano} ({arquivo})"
            )
        if arquivo.name != f"dataset_legitimo_{ano}.csv":
            raise ValueError(f"Nome inesperado para o arquivo de entrada: {arquivo}")
    return arquivos


def limpar_dataset(
    diretorio_entrada: str | Path,
    diretorio_saida: str | Path,
    arquivo_rejeitadas: str | Path,
    escopo_deduplicacao: str = "ano",
) -> tuple[int, int, int]:
    diretorio_entrada = Path(diretorio_entrada).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()
    arquivo_rejeitadas = Path(arquivo_rejeitadas).resolve()
    arquivos_entrada = _encontrar_arquivos(diretorio_entrada)

    caminhos_entrada = {arquivo.resolve() for arquivo in arquivos_entrada}
    arquivos_saida = {
        (diretorio_saida / ano / f"dataset_legitimo_{ano}.csv").resolve()
        for ano in CRAWL_POR_ANO
        if any(arquivo.parent.name == ano for arquivo in arquivos_entrada)
    }
    if arquivos_saida & caminhos_entrada or arquivo_rejeitadas in caminhos_entrada:
        raise ValueError("Os arquivos de saída não podem sobrescrever os arquivos de entrada")
    if arquivo_rejeitadas in arquivos_saida:
        raise ValueError("O relatório de rejeições não pode sobrescrever um dataset anual")

    registros_por_ano: dict[str, list[dict[str, str]]] = {
        arquivo.parent.name: [] for arquivo in arquivos_entrada
    }
    rejeitadas: list[dict[str, str | int]] = []
    chaves_vistas: set[tuple[str, ...]] = set()
    total_validas = 0
    duplicatas = 0

    for arquivo in arquivos_entrada:
        ano = arquivo.parent.name
        print(f"Processando {arquivo.name} ({CRAWL_POR_ANO[ano]})...")
        try:
            with arquivo.open("r", newline="", encoding="utf-8-sig") as entrada:
                leitor = csv.DictReader(entrada)
                if not leitor.fieldnames or not {"url", "dominio"}.issubset(leitor.fieldnames):
                    raise ValueError(
                        f"{arquivo} precisa conter as colunas 'url' e 'dominio'"
                    )

                for linha, registro in enumerate(leitor, start=2):
                    url = (registro.get("url") or "").strip()
                    dominio = (registro.get("dominio") or "").strip()
                    motivo = ""
                    url_normalizada = ""
                    host = ""
                    is_ip_host = False

                    if not url:
                        motivo = "URL vazia"
                    elif not dominio:
                        motivo = "domínio vazio"
                    else:
                        try:
                            url_normalizada, host, is_ip_host = _normalizar_url(url)
                            dominio = _normalizar_dominio(dominio)
                            if is_ip_host:
                                motivo = "hostname é um endereço IP"
                            elif not _hostname_pertence_ao_dominio(host, dominio):
                                motivo = (
                                    f"hostname '{host}' não corresponde "
                                    f"ao domínio declarado '{dominio}'"
                                )
                        except ValueError as erro:
                            motivo = str(erro)

                    if motivo:
                        rejeitadas.append(
                            {
                                "arquivo": str(arquivo.relative_to(diretorio_entrada)),
                                "linha": linha,
                                "motivo": motivo,
                                "url": url,
                                "dominio": dominio,
                            }
                        )
                        continue

                    total_validas += 1
                    if escopo_deduplicacao == "global":
                        chave = (url_normalizada,)
                    elif escopo_deduplicacao == "ano":
                        chave = (ano, url_normalizada)
                    else:
                        chave = ()

                    if chave and chave in chaves_vistas:
                        duplicatas += 1
                        continue
                    if chave:
                        chaves_vistas.add(chave)

                    registros_por_ano[ano].append(
                        {
                            "ano": ano,
                            "URL": url_normalizada,
                            "URL_não_normalizada": url,
                        }
                    )
        except (OSError, UnicodeError, csv.Error) as erro:
            raise RuntimeError(f"Falha ao ler o arquivo CSV '{arquivo}': {erro}") from erro

    diretorio_saida.mkdir(parents=True, exist_ok=True)
    arquivo_rejeitadas.parent.mkdir(parents=True, exist_ok=True)

    for ano, registros in registros_por_ano.items():
        arquivo_saida = diretorio_saida / ano / f"dataset_legitimo_{ano}.csv"
        arquivo_saida.parent.mkdir(parents=True, exist_ok=True)
        with arquivo_saida.open("w", newline="", encoding="utf-8") as saida:
            escritor = csv.DictWriter(saida, fieldnames=CAMPOS_SAIDA)
            escritor.writeheader()
            escritor.writerows(registros)

    with arquivo_rejeitadas.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS_REJEITADAS)
        escritor.writeheader()
        escritor.writerows(rejeitadas)

    return total_validas, duplicatas, len(rejeitadas)


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parents[1]
    diretorio_commoncrawl = raiz_projeto / "Common Crawl"
    parser = argparse.ArgumentParser(
        description=(
            "Valida, normaliza e deduplica os CSVs anuais extraídos do Common Crawl."
        )
    )
    parser.add_argument(
        "--entrada",
        type=Path,
        default=diretorio_commoncrawl,
        help="Pasta que contém as subpastas anuais com dataset_legitimo_<ano>.csv.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=diretorio_commoncrawl / "dataset_commoncrawl_limpo",
        help="Pasta de saída; cria uma subpasta por ano com dataset_legitimo_<ano>.csv.",
    )
    parser.add_argument(
        "--rejeitadas",
        type=Path,
        default=diretorio_commoncrawl / "dataset_commoncrawl_rejeitadas.csv",
        help="Caminho do relatório de linhas rejeitadas.",
    )
    parser.add_argument(
        "--deduplicacao",
        choices=("global", "ano", "nenhuma"),
        default="ano",
        help=(
            "Escopo da deduplicação por URL normalizada. "
            "'global' mantém a ocorrência mais antiga; 'ano' deduplica dentro "
            "de cada ano; 'nenhuma' mantém todas."
        ),
    )
    argumentos = parser.parse_args()

    validas, duplicatas, rejeitadas = limpar_dataset(
        argumentos.entrada,
        argumentos.saida,
        argumentos.rejeitadas,
        argumentos.deduplicacao,
    )
    total_saida = validas - duplicatas
    print(f"URLs válidas antes da deduplicação: {validas}")
    print(f"Duplicatas removidas: {duplicatas}")
    print(f"Linhas rejeitadas: {rejeitadas}")
    print(f"Registros escritos: {total_saida}")
    print(f"Datasets anuais: {argumentos.saida.resolve()}")
    print(f"Relatório de rejeições: {argumentos.rejeitadas.resolve()}")


if __name__ == "__main__":
    main()
