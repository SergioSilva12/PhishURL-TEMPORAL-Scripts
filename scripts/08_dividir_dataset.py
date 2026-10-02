import argparse
from pathlib import Path

import duckdb


ANOS_TREINO = (2019, 2020, 2021, 2022)
ANOS_VALIDACAO = (2023,)
ANOS_TESTE = (2024, 2025)


def _sql_literal(valor: str | Path) -> str:
    return "'" + str(valor).replace("'", "''") + "'"


def _sql_identificador(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def _origem_parquet(arquivo: Path) -> str:
    return f"read_parquet({_sql_literal(arquivo)})"


def _contagem(conexao: duckdb.DuckDBPyConnection, consulta: str) -> int:
    return int(conexao.execute(consulta).fetchone()[0])


def _contagens_por_ano(
    conexao: duckdb.DuckDBPyConnection,
    relacao: str,
    coluna_ano: str,
    coluna_label: str,
) -> dict[int, dict[str, int]]:
    ano = _sql_identificador(coluna_ano)
    label = _sql_identificador(coluna_label)
    linhas = conexao.execute(
        f"""
        SELECT
            TRY_CAST({ano} AS INTEGER) AS ano,
            COUNT(*) FILTER (WHERE TRY_CAST({label} AS INTEGER) = 1) AS phishing,
            COUNT(*) FILTER (WHERE TRY_CAST({label} AS INTEGER) = 0) AS legitimo,
            COUNT(*) AS total
        FROM {relacao}
        GROUP BY 1
        """
    ).fetchall()
    return {
        int(ano_linha): {
            "phishing": int(phishing),
            "legitimo": int(legitimo),
            "total": int(total),
        }
        for ano_linha, phishing, legitimo, total in linhas
        if ano_linha is not None
    }


def _imprimir_tabela_anos(
    dados_antes: dict[int, dict[str, int]],
    dados_depois: dict[int, dict[str, int]],
) -> None:
    print("\nContagens por ano: antes e depois da limpeza")
    print(
        f"{'Ano':<6}{'Ant. P':>8}{'Ant. L':>8}{'Ant. total':>12}"
        f"{'Final P':>9}{'Final L':>9}{'Final total':>13}{'% P final':>10}"
    )
    print("-" * 75)
    anos = sorted(set(dados_antes) | set(dados_depois))
    for ano in anos:
        antes = dados_antes.get(ano, {})
        depois = dados_depois.get(ano, {})
        total_depois = depois.get("total", 0)
        percentual = (
            depois.get("phishing", 0) / total_depois * 100
            if total_depois
            else 0.0
        )
        print(
            f"{ano:<6}"
            f"{antes.get('phishing', 0):>8,}"
            f"{antes.get('legitimo', 0):>8,}"
            f"{antes.get('total', 0):>12,}"
            f"{depois.get('phishing', 0):>9,}"
            f"{depois.get('legitimo', 0):>9,}"
            f"{total_depois:>13,}"
            f"{percentual:>9.2f}%"
        )


def _imprimir_relatorio_splits(
    dados_depois: dict[int, dict[str, int]],
) -> None:
    splits = (
        ("train", ANOS_TREINO),
        ("val", ANOS_VALIDACAO),
        ("test", ANOS_TESTE),
    )
    print("\nResumo dos splits")
    print(
        f"{'Split':<8}{'Anos':<18}{'Total':>12}"
        f"{'Phishing':>12}{'Legítimo':>12}{'% phishing':>14}"
    )
    print("-" * 76)
    for nome, anos in splits:
        phishing = sum(dados_depois.get(ano, {}).get("phishing", 0) for ano in anos)
        legitimo = sum(dados_depois.get(ano, {}).get("legitimo", 0) for ano in anos)
        total = sum(dados_depois.get(ano, {}).get("total", 0) for ano in anos)
        percentual = phishing / total * 100 if total else 0.0
        anos_texto = ", ".join(str(ano) for ano in anos)
        print(
            f"{nome:<8}{anos_texto:<18}{total:>12,}"
            f"{phishing:>12,}{legitimo:>12,}{percentual:>13.2f}%"
        )


def _copiar_split(
    conexao: duckdb.DuckDBPyConnection,
    arquivo_saida: Path,
    filtro_ano: str,
    coluna_url: str,
    seed: int,
) -> None:
    url = _sql_identificador(coluna_url)
    caminho = _sql_literal(arquivo_saida)
    consulta = f"""
        COPY (
            SELECT *
            FROM dataset_limpo
            WHERE {filtro_ano}
            ORDER BY hash(CAST({seed} AS VARCHAR) || ':' || CAST({url} AS VARCHAR)), {url}
        )
        TO {caminho}
        (FORMAT PARQUET, OVERWRITE_OR_IGNORE TRUE)
    """
    conexao.execute(consulta)


def _verificar_splits(
    conexao: duckdb.DuckDBPyConnection,
    arquivos: dict[str, Path],
    linhas_apos_limpeza: int,
    coluna_url: str,
    coluna_ano: str,
    coluna_label: str,
    coluna_classificacao: str,
) -> list[str]:
    falhas: list[str] = []
    origens = {
        nome: _origem_parquet(arquivo)
        for nome, arquivo in arquivos.items()
    }
    url = _sql_identificador(coluna_url)
    ano = _sql_identificador(coluna_ano)
    label = _sql_identificador(coluna_label)
    classificacao = _sql_identificador(coluna_classificacao)

    print("\nVerificações finais")
    pares = (("train", "val"), ("train", "test"), ("val", "test"))
    for primeiro, segundo in pares:
        em_comum = _contagem(
            conexao,
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT {url} FROM {origens[primeiro]}
                INTERSECT
                SELECT {url} FROM {origens[segundo]}
            ) AS intersecao
            """,
        )
        passou = em_comum == 0
        print(
            f"URLs em comum {primeiro} x {segundo}: "
            f"{'OK' if passou else 'FALHA'} ({em_comum:,})"
        )
        if not passou:
            falhas.append(f"URLs em comum entre {primeiro} e {segundo}: {em_comum}")

    anos_esperados = {
        "train": set(ANOS_TREINO),
        "val": set(ANOS_VALIDACAO),
        "test": set(ANOS_TESTE),
    }
    for nome, origem in origens.items():
        valores_ano = conexao.execute(
            f"SELECT DISTINCT TRY_CAST({ano} AS INTEGER) FROM {origem} ORDER BY 1"
        ).fetchall()
        anos_encontrados = {linha[0] for linha in valores_ano}
        passou = anos_encontrados == anos_esperados[nome]
        print(
            f"Anos em {nome}: {'OK' if passou else 'FALHA'} "
            f"(encontrados={sorted(anos_encontrados)}, "
            f"esperados={sorted(anos_esperados[nome])})"
        )
        if not passou:
            falhas.append(
                f"Anos incorretos em {nome}: {sorted(anos_encontrados)}"
            )

        inconsistencias = _contagem(
            conexao,
            f"""
            SELECT COUNT(*)
            FROM {origem}
            WHERE NOT COALESCE(
                (TRY_CAST({label} AS INTEGER) = 1
                 AND LOWER(TRIM(CAST({classificacao} AS VARCHAR))) = 'phishing')
                OR
                (TRY_CAST({label} AS INTEGER) = 0
                 AND LOWER(TRIM(CAST({classificacao} AS VARCHAR))) = 'legitimo'),
                FALSE
            )
            """,
        )
        passou = inconsistencias == 0
        print(
            f"Consistência label/classificacao em {nome}: "
            f"{'OK' if passou else 'FALHA'} ({inconsistencias:,})"
        )
        if not passou:
            falhas.append(
                f"Inconsistências label/classificacao em {nome}: {inconsistencias}"
            )

        phishing, legitimo, urls_vazias = conexao.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE TRY_CAST({label} AS INTEGER) = 1),
                COUNT(*) FILTER (WHERE TRY_CAST({label} AS INTEGER) = 0),
                COUNT(*) FILTER (
                    WHERE {url} IS NULL OR TRIM(CAST({url} AS VARCHAR)) = ''
                )
            FROM {origem}
            """
        ).fetchone()
        classes_presentes = phishing > 0 and legitimo > 0
        urls_presentes = urls_vazias == 0
        print(
            f"Classes em {nome}: {'OK' if classes_presentes else 'FALHA'} "
            f"(phishing={phishing:,}, legítimo={legitimo:,})"
        )
        print(
            f"URLs não vazias em {nome}: "
            f"{'OK' if urls_presentes else 'FALHA'} ({urls_vazias:,} vazias)"
        )
        if not classes_presentes:
            falhas.append(f"Uma das classes está ausente em {nome}")
        if not urls_presentes:
            falhas.append(f"URLs vazias em {nome}: {urls_vazias}")

    soma_splits = sum(
        _contagem(conexao, f"SELECT COUNT(*) FROM {origem}")
        for origem in origens.values()
    )
    passou = soma_splits == linhas_apos_limpeza
    print(
        "Soma das linhas dos splits = total após limpeza: "
        f"{'OK' if passou else 'FALHA'} "
        f"(splits={soma_splits:,}, esperado={linhas_apos_limpeza:,})"
    )
    if not passou:
        falhas.append(
            f"Soma dos splits {soma_splits} difere do total limpo {linhas_apos_limpeza}"
        )
    return falhas


def dividir_dataset(
    arquivo_entrada: str | Path,
    diretorio_saida: str | Path,
    seed: int = 42,
) -> None:
    arquivo_entrada = Path(arquivo_entrada).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()
    if not arquivo_entrada.is_file():
        raise FileNotFoundError(f"Parquet de entrada não encontrado: {arquivo_entrada}")
    diretorio_saida.mkdir(parents=True, exist_ok=True)

    conexao = duckdb.connect(database=":memory:")
    try:
        conexao.execute(
            f"CREATE TEMP VIEW dataset_origem AS "
            f"SELECT * FROM {_origem_parquet(arquivo_entrada)}"
        )
        esquema = conexao.execute(
            "DESCRIBE SELECT * FROM dataset_origem"
        ).fetchall()
        colunas = [linha[0] for linha in esquema]
        colunas_por_nome = {coluna.casefold(): coluna for coluna in colunas}
        obrigatorias = {"url", "ano", "label", "classificacao"}
        faltantes = obrigatorias - set(colunas_por_nome)
        if faltantes:
            raise ValueError(
                "Colunas obrigatórias ausentes: " + ", ".join(sorted(faltantes))
            )

        coluna_url = colunas_por_nome["url"]
        coluna_ano = colunas_por_nome["ano"]
        coluna_label = colunas_por_nome["label"]
        coluna_classificacao = colunas_por_nome["classificacao"]
        url = _sql_identificador(coluna_url)
        ano = _sql_identificador(coluna_ano)
        label = _sql_identificador(coluna_label)

        linhas_antes = _contagem(
            conexao, "SELECT COUNT(*) FROM dataset_origem"
        )
        urls_repetidas, linhas_duplicadas_antes = conexao.execute(
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(quantidade - 1), 0)
            FROM (
                SELECT COUNT(*) AS quantidade
                FROM dataset_origem
                WHERE {url} IS NOT NULL
                  AND TRIM(CAST({url} AS VARCHAR)) <> ''
                GROUP BY {url}
                HAVING COUNT(*) > 1
            ) AS duplicatas
            """
        ).fetchone()
        conexao.execute(
            f"""
            CREATE TEMP VIEW urls_conflitantes AS
            SELECT {url}
            FROM dataset_origem
            WHERE {url} IS NOT NULL
              AND TRIM(CAST({url} AS VARCHAR)) <> ''
            GROUP BY {url}
            HAVING COUNT(DISTINCT CASE
                WHEN TRY_CAST({label} AS INTEGER) IN (0, 1)
                THEN TRY_CAST({label} AS INTEGER)
            END) = 2
            """
        )
        urls_conflito = _contagem(
            conexao, "SELECT COUNT(*) FROM urls_conflitantes"
        )
        linhas_conflito = _contagem(
            conexao,
            f"""
            SELECT COUNT(*)
            FROM dataset_origem AS origem
            WHERE EXISTS (
                SELECT 1
                FROM urls_conflitantes AS conflito
                WHERE conflito.{url} = origem.{url}
            )
            """,
        )

        dados_antes = _contagens_por_ano(
            conexao, "dataset_origem", coluna_ano, coluna_label
        )
        print(f"Linhas antes da limpeza: {linhas_antes:,}")
        print(
            f"URLs repetidas antes da limpeza: {urls_repetidas:,} "
            f"(linhas excedentes: {linhas_duplicadas_antes:,})"
        )
        print(f"URLs presentes nas duas classes: {urls_conflito:,}")

        conexao.execute(
            """
            CREATE TEMP VIEW apos_remover_conflitos AS
            SELECT origem.*
            FROM dataset_origem AS origem
            WHERE NOT EXISTS (
                SELECT 1
                FROM urls_conflitantes AS conflito
                WHERE conflito.URL = origem.URL
            )
            """
        )
        linhas_apos_conflitos = _contagem(
            conexao, "SELECT COUNT(*) FROM apos_remover_conflitos"
        )

        alias_linha = "__dividir_rn"
        while alias_linha.casefold() in colunas_por_nome:
            alias_linha += "_"
        projecao = ", ".join(_sql_identificador(coluna) for coluna in colunas)
        alias_sql = _sql_identificador(alias_linha)
        conexao.execute(
            f"""
            CREATE TEMP VIEW dataset_limpo AS
            SELECT {projecao}
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {url}
                    ORDER BY {ano}
                ) AS {alias_sql}
                FROM apos_remover_conflitos
            ) AS ordenado
            WHERE {alias_sql} = 1
            """
        )
        linhas_apos_limpeza = _contagem(
            conexao, "SELECT COUNT(*) FROM dataset_limpo"
        )
        linhas_duplicadas_removidas = (
            linhas_apos_conflitos - linhas_apos_limpeza
        )
        print(
            f"Linhas removidas por conflito de rótulo: {linhas_conflito:,} "
            f"({urls_conflito:,} URLs)"
        )
        print(
            f"Linhas removidas por duplicata: {linhas_duplicadas_removidas:,}"
        )
        print(f"Total após limpeza: {linhas_apos_limpeza:,}")

        dados_depois = _contagens_por_ano(
            conexao, "dataset_limpo", coluna_ano, coluna_label
        )
        _imprimir_tabela_anos(dados_antes, dados_depois)
        _imprimir_relatorio_splits(dados_depois)

        arquivos = {
            "train": diretorio_saida / "train.parquet",
            "val": diretorio_saida / "val.parquet",
            "test": diretorio_saida / "test.parquet",
        }
        _copiar_split(
            conexao,
            arquivos["train"],
            f"TRY_CAST({ano} AS INTEGER) BETWEEN 2019 AND 2022",
            coluna_url,
            seed,
        )
        _copiar_split(
            conexao,
            arquivos["val"],
            f"TRY_CAST({ano} AS INTEGER) = 2023",
            coluna_url,
            seed,
        )
        _copiar_split(
            conexao,
            arquivos["test"],
            f"TRY_CAST({ano} AS INTEGER) BETWEEN 2024 AND 2025",
            coluna_url,
            seed,
        )
        print(f"\nArquivos salvos em: {diretorio_saida}")
        for nome, arquivo in arquivos.items():
            print(f"  - {nome}: {arquivo}")

        falhas = _verificar_splits(
            conexao,
            arquivos,
            linhas_apos_limpeza,
            coluna_url,
            coluna_ano,
            coluna_label,
            coluna_classificacao,
        )
        if falhas:
            print("\nAVISO: uma ou mais verificações falharam:")
            for falha in falhas:
                print(f"  - {falha}")
            raise SystemExit(1)
        print("\nTodas as verificações finais passaram.")
    finally:
        conexao.close()


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Divide o Parquet total em train, val e test por ano."
    )
    parser.add_argument(
        "--entrada",
        type=Path,
        default=raiz_projeto / "dataset_total" / "dataset_total.parquet",
        help="Parquet total que será limpo e dividido.",
    )
    parser.add_argument(
        "--saida-dir",
        type=Path,
        default=raiz_projeto / "DATASET",
        help="Pasta de saída dos arquivos train.parquet, val.parquet e test.parquet.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed para a ordenação determinística dentro dos splits.",
    )
    argumentos = parser.parse_args()
    try:
        dividir_dataset(argumentos.entrada, argumentos.saida_dir, argumentos.seed)
    except SystemExit:
        raise
    except Exception as erro:
        print(f"ERRO: divisão não concluída: {erro}")
        raise SystemExit(1) from erro


if __name__ == "__main__":
    main()
