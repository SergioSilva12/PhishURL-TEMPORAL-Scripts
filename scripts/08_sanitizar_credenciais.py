import argparse
import csv
from pathlib import Path

import duckdb


RAIZ_PROJETO = Path(__file__).resolve().parents[1]
PADROES_CREDENCIAIS = (
    (
        "AWS access key ID",
        r"\b(?:AKIA|ASIA|AIDA|AROA|ANPA|ANVA|AGPA)[A-Z0-9]{16}\b",
    ),
    (
        "AWS secret/session token",
        r"(?i)(?:aws_secret_access_key|aws_session_token)\s*[:=]\s*[\"']?[^&#\s\"'<>]{8,}",
    ),
    (
        "Google API/service-account credentials",
        r"\bAIza[0-9A-Za-z_-]{35}\b|ya29\.[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\"private_key\"\s*:",
    ),
    (
        "Azure storage/SAS/client secrets",
        r"(?i)(?:AccountKey|SharedAccessSignature|client_secret)\s*[:=]\s*[^;?&#\s]+|[?&]sig=[^&#\s]+",
    ),
    (
        "GitHub personal/OAuth tokens",
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{20,})\b",
    ),
    (
        "GitLab access tokens",
        r"\b(?:glpat|gldt|glrt)-[A-Za-z0-9_-]{20,}\b",
    ),
    (
        "Slack tokens/webhooks",
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b|https?://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+",
    ),
    (
        "Discord tokens/webhooks",
        r"\bmfa\.[A-Za-z0-9_-]{20,}\b|\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b|https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]{20,}",
    ),
    (
        "OpenAI/API keys",
        r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b",
    ),
    (
        "Stripe secret/restricted keys",
        r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b",
    ),
    (
        "Twilio account/auth credentials",
        r"\bAC[0-9a-fA-F]{32}\b|(?i)auth_token\s*[:=]\s*[\"']?[^&#\s\"'<>]{8,}",
    ),
    (
        "SendGrid API keys",
        r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{32,}\b",
    ),
    (
        "Firebase tokens/keys",
        r"\bAAAA[A-Za-z0-9_-]{7,}:APA91[A-Za-z0-9_-]{20,}\b|(?i)firebase[^\s]{0,40}(?:token|key)\s*[:=]\s*[\"']?[^&#\s\"'<>]{8,}",
    ),
    (
        "JWT tokens",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    ),
    (
        "OAuth/API/password/token parameters",
        r"(?i)(?:password|passwd|pwd|pass|secret|token|access_token|refresh_token|client_secret|api_key|apikey|auth_token|aws_access_key_id)\s*[:=]\s*[\"']?[^&#\s\"'<>]{8,}",
    ),
    (
        "Database URLs with username/password",
        r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?|mssql|jdbc):\/\/[^\/@:\s]+:[^\/@\s]+@",
    ),
    (
        "Private SSH/certificate keys",
        r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----|-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ),
    (
        "Secret-bearing webhook URLs",
        r"(?i)https?://[^/\s]*(?:webhook|webhooks)[^/\s]*/[^\s?#]{16,}",
    ),
)


CAMPOS_RELATORIO = [
    "arquivo",
    "linhas_entrada",
    "linhas_removidas",
    "urls_removidas_unicas",
    "linhas_saida",
    *[nome for nome, _ in PADROES_CREDENCIAIS],
]


def _literal_sql(valor: str | Path) -> str:
    return "'" + str(valor).replace("'", "''") + "'"


def _identificador_sql(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def _condicao_padrao(colunas_texto: list[str], padrao: str) -> str:
    verificacoes = [
        "regexp_matches(COALESCE(CAST("
        + _identificador_sql(coluna)
        + " AS VARCHAR), ''), "
        + _literal_sql(padrao)
        + ")"
        for coluna in colunas_texto
    ]
    return "(" + " OR ".join(verificacoes) + ")" if verificacoes else "FALSE"


def _validar_diretorios(entrada: Path, saida: Path) -> None:
    if not entrada.is_dir():
        raise NotADirectoryError(f"Pasta de entrada não encontrada: {entrada}")
    if entrada == saida or entrada.is_relative_to(saida) or saida.is_relative_to(entrada):
        raise ValueError(
            "As pastas de entrada e saída não podem ser iguais nem sobrepostas. "
            "Use uma pasta de saída separada para preservar os originais."
        )


def _processar_arquivo(
    conexao: duckdb.DuckDBPyConnection,
    arquivo_entrada: Path,
    arquivo_saida: Path,
) -> dict[str, int | str]:
    origem = f"read_parquet({_literal_sql(arquivo_entrada)})"
    esquema = conexao.execute(f"DESCRIBE SELECT * FROM {origem}").fetchall()
    colunas = [linha[0] for linha in esquema]
    colunas_texto = [
        nome
        for nome, tipo, *_ in esquema
        if tipo.upper().startswith(("VARCHAR", "CHAR", "TEXT"))
    ]
    colunas_url = [nome for nome in colunas if nome.casefold() == "url"]
    if not colunas_url:
        raise ValueError(f"O arquivo não possui coluna URL: {arquivo_entrada}")
    coluna_url = _identificador_sql(colunas_url[0])

    condicoes = [
        _condicao_padrao(colunas_texto, padrao)
        for _, padrao in PADROES_CREDENCIAIS
    ]
    condicao_geral = "(" + " OR ".join(condicoes) + ")"
    expressoes = [
        "COUNT(*)",
        f"COUNT(*) FILTER (WHERE {condicao_geral})",
        f"COUNT(DISTINCT {coluna_url}) FILTER (WHERE {condicao_geral})",
        f"COUNT(*) FILTER (WHERE NOT {condicao_geral})",
        *[
            f"COUNT(*) FILTER (WHERE {condicao})"
            for condicao in condicoes
        ],
    ]
    metricas = conexao.execute(
        f"SELECT {', '.join(expressoes)} FROM {origem}"
    ).fetchone()
    total_entrada = int(metricas[0])
    removidas = int(metricas[1])
    urls_removidas = int(metricas[2])
    total_saida = int(metricas[3])
    contagens_categoria = [int(valor) for valor in metricas[4:]]

    arquivo_saida.parent.mkdir(parents=True, exist_ok=True)
    conexao.execute(
        f"""
        COPY (
            SELECT *
            FROM {origem}
            WHERE NOT {condicao_geral}
        ) TO {_literal_sql(arquivo_saida)}
        (FORMAT PARQUET, OVERWRITE_OR_IGNORE TRUE)
        """
    )
    origem_saida = f"read_parquet({_literal_sql(arquivo_saida)})"
    linhas_sinalizadas_restantes = conexao.execute(
        f"SELECT COUNT(*) FROM {origem_saida} WHERE {condicao_geral}"
    ).fetchone()[0]
    if linhas_sinalizadas_restantes:
        raise RuntimeError(
            f"{linhas_sinalizadas_restantes} linhas sinalizadas permaneceram em "
            f"{arquivo_saida}"
        )

    return {
        "arquivo": arquivo_entrada.name,
        "linhas_entrada": total_entrada,
        "linhas_removidas": removidas,
        "urls_removidas_unicas": urls_removidas,
        "linhas_saida": total_saida,
        **{
            nome: quantidade
            for (nome, _), quantidade in zip(
                PADROES_CREDENCIAIS,
                contagens_categoria,
            )
        },
    }


def sanitizar_dataset(
    diretorio_entrada: str | Path,
    diretorio_saida: str | Path,
) -> None:
    diretorio_entrada = Path(diretorio_entrada).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()
    _validar_diretorios(diretorio_entrada, diretorio_saida)

    arquivos = sorted(diretorio_entrada.glob("*.parquet"))
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum arquivo .parquet encontrado diretamente em {diretorio_entrada}"
        )

    conexao = duckdb.connect(database=":memory:")
    relatorios = []
    try:
        for arquivo in arquivos:
            arquivo_saida = diretorio_saida / arquivo.name
            relatorio = _processar_arquivo(conexao, arquivo, arquivo_saida)
            relatorios.append(relatorio)
            percentual = (
                relatorio["linhas_removidas"] / relatorio["linhas_entrada"] * 100
                if relatorio["linhas_entrada"]
                else 0.0
            )
            print(
                f"{arquivo.name}: entrada={relatorio['linhas_entrada']:,}, "
                f"removidas={relatorio['linhas_removidas']:,} "
                f"({percentual:.4f}%), "
                f"URLs removidas únicas={relatorio['urls_removidas_unicas']:,}, "
                f"saída={relatorio['linhas_saida']:,}"
            )
            deteccoes = [
                f"{nome}: {relatorio[nome]:,}"
                for nome, _ in PADROES_CREDENCIAIS
                if relatorio[nome]
            ]
            if deteccoes:
                print("  Categorias: " + "; ".join(deteccoes))
    finally:
        conexao.close()

    arquivo_relatorio = diretorio_saida / "relatorio_remocao_credenciais.csv"
    with arquivo_relatorio.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS_RELATORIO)
        escritor.writeheader()
        escritor.writerows(relatorios)

    relatorio_total = next(
        (linha for linha in relatorios if linha["arquivo"] == "dataset_total.parquet"),
        None,
    )
    relatorios_anuais = [
        linha
        for linha in relatorios
        if str(linha["arquivo"]).removesuffix(".parquet").isdigit()
    ]
    print("\nResumo de remoções")
    print(
        "Removidas no dataset_total.parquet (sem somar cópias anuais): "
        f"{relatorio_total['linhas_removidas']:,} linhas / "
        f"{relatorio_total['urls_removidas_unicas']:,} URLs únicas"
        if relatorio_total
        else "Arquivo dataset_total.parquet não encontrado; resumo agregado indisponível."
    )
    if relatorio_total:
        deteccoes_totais = [
            f"{nome}: {relatorio_total[nome]:,}"
            for nome, _ in PADROES_CREDENCIAIS
            if relatorio_total[nome]
        ]
        if deteccoes_totais:
            print("Categorias detectadas no total: " + "; ".join(deteccoes_totais))
    if relatorios_anuais:
        total_anuais = sum(int(linha["linhas_removidas"]) for linha in relatorios_anuais)
        print(
            "Removidas somando os arquivos anuais (contagem separada por arquivo): "
            f"{total_anuais:,} linhas"
        )
    print(f"Relatório detalhado: {arquivo_relatorio}")
    print(f"Cópias sanitizadas: {diretorio_saida}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Remove dos Parquets as linhas com credenciais ou segredos expostos."
        )
    )
    parser.add_argument(
        "--entrada-dir",
        type=Path,
        default=RAIZ_PROJETO / "dataset_total",
        help="Pasta com os Parquets originais (padrão: dataset_total).",
    )
    parser.add_argument(
        "--saida-dir",
        type=Path,
        default=RAIZ_PROJETO / "dataset_total_sanitizado",
        help="Pasta separada para os Parquets filtrados.",
    )
    argumentos = parser.parse_args()
    try:
        sanitizar_dataset(argumentos.entrada_dir, argumentos.saida_dir)
    except Exception as erro:
        raise SystemExit(f"ERRO: sanitização não concluída: {erro}") from erro


if __name__ == "__main__":
    main()
