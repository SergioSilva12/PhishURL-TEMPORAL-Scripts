import argparse
from pathlib import Path

import duckdb


ANOS = tuple(range(2019, 2026))


def _literal_sql(valor: str | Path) -> str:
    return "'" + str(valor).replace("'", "''") + "'"


def _identificador_sql(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def _origem_parquet(arquivo: Path) -> str:
    return f"read_parquet({_literal_sql(arquivo)})"


def _exigir_arquivo(arquivo: Path) -> None:
    if not arquivo.is_file():
        raise FileNotFoundError(f"Arquivo Parquet não encontrado: {arquivo}")


def _inspecionar_arquivo(
    conexao: duckdb.DuckDBPyConnection,
    arquivo: Path,
    titulo: str,
) -> dict[str, int]:
    _exigir_arquivo(arquivo)
    origem = _origem_parquet(arquivo)
    esquema = conexao.execute(f"DESCRIBE SELECT * FROM {origem}").fetchall()
    colunas = [linha[0] for linha in esquema]
    colunas_normalizadas = {coluna.casefold(): coluna for coluna in colunas}
    obrigatorias = {"url", "label"}
    faltantes = obrigatorias - set(colunas_normalizadas)
    if faltantes:
        raise ValueError(
            f"{arquivo} não contém as colunas obrigatórias: {', '.join(sorted(faltantes))}"
        )

    coluna_url = _identificador_sql(colunas_normalizadas["url"])
    coluna_label = _identificador_sql(colunas_normalizadas["label"])
    print(f"\n{'=' * 78}\n{titulo}: {arquivo.name}\n{'=' * 78}")
    print("Colunas e tipos:")
    for nome, tipo, *_ in esquema:
        print(f"  - {nome}: {tipo}")

    total, phishing, legitimo = conexao.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE TRY_CAST({coluna_label} AS INTEGER) = 1),
            COUNT(*) FILTER (WHERE TRY_CAST({coluna_label} AS INTEGER) = 0)
        FROM {origem}
        """
    ).fetchone()
    phishing_pct = phishing / total * 100 if total else 0.0
    legitimo_pct = legitimo / total * 100 if total else 0.0
    print(f"Linhas: {total:,}")
    print(
        f"Rótulos: phishing={phishing:,} ({phishing_pct:.2f}%), "
        f"legítimo={legitimo:,} ({legitimo_pct:.2f}%)"
    )

    urls_unicas, linhas_repetidas, grupos_repetidos = conexao.execute(
        f"""
        SELECT
            COUNT(DISTINCT {coluna_url}),
            COUNT(*) FILTER (
                WHERE {coluna_url} IS NOT NULL
                  AND TRIM(CAST({coluna_url} AS VARCHAR)) <> ''
            ) - COUNT(DISTINCT {coluna_url}),
            (
                SELECT COUNT(*)
                FROM (
                    SELECT {coluna_url}
                    FROM {origem}
                    WHERE {coluna_url} IS NOT NULL
                      AND TRIM(CAST({coluna_url} AS VARCHAR)) <> ''
                    GROUP BY {coluna_url}
                    HAVING COUNT(*) > 1
                ) AS repetidas
            )
        FROM {origem}
        """
    ).fetchone()
    print(
        f"URLs únicas: {urls_unicas:,}; URLs repetidas: {grupos_repetidos:,} "
        f"(linhas excedentes: {linhas_repetidas:,})"
    )

    print("Valores vazios por coluna:")
    for nome, *_ in esquema:
        coluna = _identificador_sql(nome)
        vazios = conexao.execute(
            f"""
            SELECT COUNT(*)
            FROM {origem}
            WHERE {coluna} IS NULL
               OR TRIM(CAST({coluna} AS VARCHAR)) = ''
            """
        ).fetchone()[0]
        print(f"  - {nome}: {vazios:,}")

    if "classificacao" not in colunas_normalizadas:
        print("Exemplos por classe: coluna classificacao ausente.")
    else:
        coluna_classificacao = _identificador_sql(
            colunas_normalizadas["classificacao"]
        )
        print("Exemplos de URL por classe:")
        for label, nome_classe in ((1, "phishing"), (0, "legítimo")):
            exemplos = conexao.execute(
                f"""
                SELECT {coluna_url}
                FROM {origem}
                WHERE TRY_CAST({coluna_label} AS INTEGER) = ?
                LIMIT 3
                """,
                [label],
            ).fetchall()
            print(f"  {nome_classe}:")
            if not exemplos:
                print("    (sem registros)")
            for (url,) in exemplos:
                print(f"    - {url}")

    return {
        "total": total,
        "phishing": phishing,
        "legitimo": legitimo,
        "urls_unicas": urls_unicas,
        "linhas_repetidas": linhas_repetidas,
        "grupos_repetidos": grupos_repetidos,
    }


def _verificar_total(
    conexao: duckdb.DuckDBPyConnection,
    arquivo_total: Path,
    arquivos_anuais: list[Path],
    resumos_anuais: list[dict[str, int]],
) -> None:
    _exigir_arquivo(arquivo_total)
    origem = _origem_parquet(arquivo_total)
    esquema = conexao.execute(f"DESCRIBE SELECT * FROM {origem}").fetchall()
    colunas = {linha[0].casefold(): linha[0] for linha in esquema}
    necessarias = {"url", "ano", "label", "classificacao"}
    faltantes = necessarias - set(colunas)
    if faltantes:
        print(
            "Verificações globais incompletas; colunas ausentes no total: "
            f"{', '.join(sorted(faltantes))}"
        )
        return

    url = _identificador_sql(colunas["url"])
    ano = _identificador_sql(colunas["ano"])
    label = _identificador_sql(colunas["label"])
    classificacao = _identificador_sql(colunas["classificacao"])

    total = conexao.execute(f"SELECT COUNT(*) FROM {origem}").fetchone()[0]
    soma_anuais = sum(resumo["total"] for resumo in resumos_anuais)
    print(f"\n{'=' * 78}\nVerificações globais: {arquivo_total.name}\n{'=' * 78}")
    confere_soma = total == soma_anuais
    print(
        f"Soma das linhas anuais = total: {'OK' if confere_soma else 'FALHA'} "
        f"(anuais={soma_anuais:,}; total={total:,})"
    )

    urls_multiano = conexao.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT {url}
            FROM {origem}
            WHERE {url} IS NOT NULL
              AND TRIM(CAST({url} AS VARCHAR)) <> ''
            GROUP BY {url}
            HAVING COUNT(DISTINCT {ano}) > 1
        ) AS urls_multiano
        """
    ).fetchone()[0]
    print(f"URLs que aparecem em mais de um ano: {urls_multiano:,}")

    urls_duas_classes = conexao.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT {url}
            FROM {origem}
            WHERE {url} IS NOT NULL
              AND TRIM(CAST({url} AS VARCHAR)) <> ''
            GROUP BY {url}
            HAVING COUNT(DISTINCT TRY_CAST({label} AS INTEGER)) > 1
        ) AS urls_duas_classes
        """
    ).fetchone()[0]
    print(f"URLs presentes nas duas classes: {urls_duas_classes:,}")

    rotulos_inconsistentes = conexao.execute(
        f"""
        SELECT COUNT(*)
        FROM {origem}
        WHERE NOT (
            (TRY_CAST({label} AS INTEGER) = 1
             AND LOWER(TRIM(CAST({classificacao} AS VARCHAR))) = 'phishing')
            OR
            (TRY_CAST({label} AS INTEGER) = 0
             AND LOWER(TRIM(CAST({classificacao} AS VARCHAR))) = 'legitimo')
        )
        """
    ).fetchone()[0]
    print(
        "Consistência label/classificacao: "
        f"{'OK' if rotulos_inconsistentes == 0 else 'FALHA'} "
        f"({rotulos_inconsistentes:,} linhas inconsistentes)"
    )

    anos_invalidos = conexao.execute(
        f"""
        SELECT DISTINCT CAST({ano} AS VARCHAR)
        FROM {origem}
        WHERE TRY_CAST({ano} AS INTEGER) IS NULL
           OR TRY_CAST({ano} AS INTEGER) NOT BETWEEN 2019 AND 2025
        ORDER BY 1
        """
    ).fetchall()
    print(
        "Anos restritos a 2019-2025: "
        f"{'OK' if not anos_invalidos else 'FALHA'}"
        + (f" (valores fora do intervalo: {[valor[0] for valor in anos_invalidos]})" if anos_invalidos else "")
    )

    print("\nResumo anual")
    print(f"{'Ano':<8}{'Phishing':>12}{'Legítimo':>12}{'Total':>12}{'% phishing':>14}")
    print("-" * 58)
    for ano_numero, resumo in zip(ANOS, resumos_anuais):
        percentual = (
            resumo["phishing"] / resumo["total"] * 100
            if resumo["total"]
            else 0.0
        )
        print(
            f"{ano_numero:<8}{resumo['phishing']:>12,}{resumo['legitimo']:>12,}"
            f"{resumo['total']:>12,}{percentual:>13.2f}%"
        )
    print("-" * 58)
    total_phishing = sum(resumo["phishing"] for resumo in resumos_anuais)
    total_legitimo = sum(resumo["legitimo"] for resumo in resumos_anuais)
    percentual_total = total_phishing / total * 100 if total else 0.0
    print(
        f"{'Total':<8}{total_phishing:>12,}{total_legitimo:>12,}"
        f"{total:>12,}{percentual_total:>13.2f}%"
    )


def verificar_dataset(diretorio: str | Path) -> None:
    diretorio = Path(diretorio).resolve()
    conexao = duckdb.connect(database=":memory:")
    arquivos_anuais = []
    resumos_anuais = []
    try:
        for ano in ANOS:
            arquivo = diretorio / f"{ano}.parquet"
            arquivos_anuais.append(arquivo)
            resumos_anuais.append(
                _inspecionar_arquivo(conexao, arquivo, f"Dataset anual {ano}")
            )

        arquivo_total = diretorio / "dataset_total.parquet"
        _inspecionar_arquivo(conexao, arquivo_total, "Dataset total")
        _verificar_total(
            conexao,
            arquivo_total,
            arquivos_anuais,
            resumos_anuais,
        )
    finally:
        conexao.close()


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Inspeciona os Parquets anuais e total do dataset temporal."
    )
    parser.add_argument(
        "--diretorio",
        type=Path,
        default=raiz_projeto / "dataset_total",
        help="Pasta com 2019.parquet a 2025.parquet e dataset_total.parquet.",
    )
    argumentos = parser.parse_args()
    verificar_dataset(argumentos.diretorio)


if __name__ == "__main__":
    main()
