import argparse
from pathlib import Path

import duckdb


RAIZ_PROJETO = Path(__file__).resolve().parents[1]


def extrair_top_10k_dominios(arquivo_entrada: str | Path, arquivo_saida: str | Path):
    """
    Lê uma lista de domínios em CSV (formato: rank,dominio) e salva apenas os 10.000 primeiros.
    """
    entrada = Path(arquivo_entrada)
    saida = Path(arquivo_saida)

    if not entrada.exists():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {entrada}")

    print(f"Lendo '{entrada}' e extraindo os top 10.000 domínios...")

    saida.parent.mkdir(parents=True, exist_ok=True)

    entrada_sql = entrada.as_posix().replace("'", "''")
    saida_sql = saida.as_posix().replace("'", "''")

    query = f"""
    COPY (
        SELECT
            dominio
        FROM read_csv(
            '{entrada_sql}',
            header = false,
            columns = {{'rank': 'INTEGER', 'dominio': 'VARCHAR'}}
        )
        ORDER BY rank ASC
        LIMIT 10000
    ) TO '{saida_sql}' (FORMAT CSV, HEADER TRUE, DELIMITER ',');
    """

    con = duckdb.connect(database=':memory:')
    try:
        con.execute(query)
        print(f"Sucesso! Domínios salvos em: {saida}")
    except Exception as exc:
        print(f"Erro ao extrair domínios: {exc}")
        raise
    finally:
        con.close()


def extrairURL(
    arquivo_entrada: str | Path = RAIZ_PROJETO / 'Dominio TrancoLIst' / 'top-1m.csv',
    arquivo_saida: str | Path = RAIZ_PROJETO / 'Common Crawl' / 'top_10k_dominios.csv',
):
    """Alias para manter compatibilidade com o nome do script."""
    return extrair_top_10k_dominios(arquivo_entrada, arquivo_saida)


def main():
    parser = argparse.ArgumentParser(description='Extrai os 10.000 primeiros domínios de um CSV de ranking.')
    parser.add_argument(
        '--entrada',
        type=Path,
        default=RAIZ_PROJETO / 'Dominio TrancoLIst' / 'top-1m.csv',
        help='Caminho do CSV de entrada.',
    )
    parser.add_argument(
        '--saida',
        type=Path,
        default=RAIZ_PROJETO / 'Common Crawl' / 'top_10k_dominios.csv',
        help='Caminho do CSV de saída.',
    )
    args = parser.parse_args()

    extrair_top_10k_dominios(args.entrada, args.saida)


if __name__ == '__main__':
    main()