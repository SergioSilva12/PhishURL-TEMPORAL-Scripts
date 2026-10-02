import argparse
import csv
import gzip
import urllib.error
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb

# Mapeamento de crawl representativo por ano — mantenha esse critério
# consistente entre treino e teste.
CRAWL_POR_ANO = {
    "2019": "CC-MAIN-2019-51",
    "2020": "CC-MAIN-2020-50",
    "2021": "CC-MAIN-2021-49",
    "2022": "CC-MAIN-2022-49",
    "2023": "CC-MAIN-2023-50",
    "2024": "CC-MAIN-2024-51",
    "2025": "CC-MAIN-2025-51",
}

BASE_URL = "https://data.commoncrawl.org/"
RAIZ_PROJETO = Path(__file__).resolve().parents[1]


def _resolver_path(caminho: str | Path) -> Path:
    """Resolve caminhos relativos à raiz do projeto ou à pasta Common Crawl."""
    path = Path(caminho)
    if path.is_absolute():
        return path

    pasta_commoncrawl = RAIZ_PROJETO / "Common Crawl"
    candidatos = (
        RAIZ_PROJETO / path,
        pasta_commoncrawl / path.name,
        Path.cwd() / path,
    )
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return candidatos[0]


def _baixar_lista_de_parquets(crawl: str, timeout: int = 60) -> list[str]:
    """
    Baixa o manifesto (cc-index-table.paths.gz) publicado pelo Common Crawl
    para o crawl informado, e retorna a lista de URLs HTTPS completas dos
    arquivos parquet do subset 'warc' (o índice de URLs propriamente dito).

    Usa exclusivamente o domínio data.commoncrawl.org (servido via CDN
    CloudFront) — não depende do index.commoncrawl.org, que é um host
    separado (servidor EC2 direto) e mais sujeito a bloqueios de
    firewall/antivírus/roteador.
    """
    manifest_url = f"{BASE_URL}crawl-data/{crawl}/cc-index-table.paths.gz"
    print(f"Baixando manifesto de arquivos parquet: {manifest_url}")

    request = Request(manifest_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            conteudo_gz = response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Não foi possível baixar o manifesto do crawl '{crawl}' "
            f"(HTTP {e.code}). Verifique se o identificador do crawl está "
            f"correto (ex.: 'CC-MAIN-2023-50')."
        ) from e

    with gzip.open(BytesIO(conteudo_gz), "rt", encoding="utf-8") as f:
        linhas = [linha.strip() for linha in f if linha.strip()]

    # O manifesto lista caminhos relativos, ex.:
    #   cc-index/table/cc-main/warc/crawl=CC-MAIN-2023-50/subset=warc/part-00000-....c000.gz.parquet
    # Filtramos apenas os do subset=warc (URLs de páginas), ignorando
    # eventuais entradas de outros subsets (robotstxt, crawldiagnostics etc.).
    caminhos_warc = [linha for linha in linhas if "/subset=warc/" in linha]

    if not caminhos_warc:
        raise RuntimeError(
            f"O manifesto do crawl '{crawl}' não contém arquivos do "
            f"subset=warc — verifique se o crawl existe e está completo."
        )

    urls = [BASE_URL + caminho for caminho in caminhos_warc]
    print(f"Manifesto baixado: {len(urls)} arquivos parquet (subset=warc) encontrados.")
    return urls


def extrair_urls_legitimas_cc(
    ano: str,
    arquivo_dominios: str | Path,
    max_por_dominio: int = 3,
    limite_total: int = 15000,
    arquivo_saida: str | Path | None = None,
):
    """
    Extrai URLs do Índice de URLs (Columnar Index) do Common Crawl via
    HTTPS (data.commoncrawl.org), filtrando pelos domínios confiáveis,
    com seleção balanceada (round-robin) entre domínios.

    Não depende do index.commoncrawl.org nem de credenciais AWS/S3 —
    usa apenas leitura HTTPS de arquivos parquet públicos.
    """
    crawl = CRAWL_POR_ANO[ano]
    output_file = (
        _resolver_path(arquivo_saida)
        if arquivo_saida
        else (
            RAIZ_PROJETO
            / "Common Crawl"
            / ano
            / f"dataset_legitimo_{ano}.csv"
        )
    )
    arquivo_dominios_path = _resolver_path(arquivo_dominios)
    if not arquivo_dominios_path.exists():
        raise FileNotFoundError(
            f"Arquivo de domínios '{arquivo_dominios}' não encontrado. "
            "Procurei relativo ao script, na pasta 'Common Crawl' do projeto "
            f"e no diretório atual. Caminho final verificado: {arquivo_dominios_path}"
        )

    print(f"Iniciando extração do Common Crawl {crawl} (ano de referência {ano}) via parquet/HTTPS...")

    urls_parquet = _baixar_lista_de_parquets(crawl)

    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")
    temp_dir = output_file.parent / "duckdb_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
    con.execute("SET memory_limit = '6GiB'")
    con.execute("SET threads = 4")
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET max_temp_directory_size = '30GiB'")
    # O CDN pode responder 503 temporariamente ao acessar muitos parquets.
    con.execute("SET http_retries = 8")
    con.execute("SET http_retry_wait_ms = 1000")
    con.execute("SET http_retry_backoff = 2")
    con.execute("SET enable_progress_bar = true")
    con.execute("SET enable_progress_bar_print = true")
    con.execute("SET progress_bar_time = 2000")

    try:
        print("Consultando os parquets remotos e filtrando pelos domínios confiáveis "
              "(isso pode levar alguns minutos, dependendo da conexão)...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        urls_seen: set[tuple[str, str]] = set()
        dominios_contagem: dict[str, int] = {}
        tamanho_lote = 5
        lotes = [
            urls_parquet[inicio:inicio + tamanho_lote]
            for inicio in range(0, len(urls_parquet), tamanho_lote)
        ]

        with output_file.open("w", newline="", encoding="utf-8") as arquivo_csv:
            writer = csv.writer(arquivo_csv)
            writer.writerow(["url", "dominio"])

            for indice, lote in enumerate(lotes, start=1):
                paths_lote_sql = "[" + ",".join(f"'{u}'" for u in lote) + "]"
                query = f"""
                    WITH TopDominios AS (
                        SELECT DISTINCT LOWER(d.column0) AS dominio
                        FROM read_csv_auto(
                            '{arquivo_dominios_path.as_posix()}',
                            header=false
                        ) AS d
                    ),
                    MatchedURLs AS (
                        SELECT DISTINCT
                            c.url,
                            LOWER(c.url_host_registered_domain) AS dominio
                        FROM read_parquet({paths_lote_sql}) c
                        INNER JOIN TopDominios t
                            ON LOWER(c.url_host_registered_domain) = t.dominio
                        WHERE c.url IS NOT NULL
                          AND c.fetch_status = 200
                    )
                    SELECT url, dominio
                    FROM MatchedURLs
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY dominio
                        ORDER BY hash(url)
                    ) <= {max_por_dominio}
                    LIMIT {limite_total}
                """
                for url, dominio in con.execute(query).fetchall():
                    chave = (url, dominio)
                    if chave in urls_seen or dominios_contagem.get(dominio, 0) >= max_por_dominio:
                        continue
                    urls_seen.add(chave)
                    dominios_contagem[dominio] = dominios_contagem.get(dominio, 0) + 1
                    writer.writerow([url, dominio])
                    if len(urls_seen) >= limite_total:
                        break
                print(
                    f"Lote {indice}/{len(lotes)} concluído: "
                    f"{len(urls_seen)}/{limite_total} URLs selecionadas."
                )
                if len(urls_seen) >= limite_total:
                    break
        tamanho_mb = output_file.stat().st_size / (1024 * 1024)
        print(
            f"Sucesso! O dataset legítimo de {ano} foi salvo em: {output_file} "
            f"({tamanho_mb:.2f} MB)."
        )

    except Exception as e:
        print(f"Erro durante a extração do Common Crawl: {e}")
        raise
    finally:
        con.close()

    return output_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extrai URLs legítimas dos crawls Common Crawl selecionados."
    )
    parser.add_argument(
        "--anos",
        type=int,
        nargs="+",
        choices=tuple(int(ano) for ano in CRAWL_POR_ANO),
        default=tuple(int(ano) for ano in CRAWL_POR_ANO),
        help="Anos a extrair (padrão: 2019 a 2025).",
    )
    parser.add_argument(
        "--dominios",
        type=Path,
        default=RAIZ_PROJETO / "Common Crawl" / "top_10k_dominios.csv",
        help="CSV dos domínios confiáveis.",
    )
    parser.add_argument(
        "--saida-dir",
        type=Path,
        default=RAIZ_PROJETO / "Common Crawl",
        help="Pasta que receberá uma subpasta por ano.",
    )
    parser.add_argument("--max-por-dominio", type=int, default=3)
    parser.add_argument("--limite-total", type=int, default=15000)
    argumentos = parser.parse_args()

    for ano in argumentos.anos:
        arquivo_saida = (
            argumentos.saida_dir / str(ano) / f"dataset_legitimo_{ano}.csv"
        )
        extrair_urls_legitimas_cc(
            ano=str(ano),
            arquivo_dominios=argumentos.dominios,
            max_por_dominio=argumentos.max_por_dominio,
            limite_total=argumentos.limite_total,
            arquivo_saida=arquivo_saida,
        )