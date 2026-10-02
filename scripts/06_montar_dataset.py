import argparse
from pathlib import Path

import duckdb
import pandas as pd


ANOS = tuple(range(2019, 2026))
SEED_PADRAO = 42
COLUNAS_DE_ROTULO = {"label", "classificacao"}


def _localizar_arquivo_csv(
    diretorio: Path,
    ano: int,
    prefixo: str,
) -> Path:
    candidatos = (
        diretorio / str(ano) / f"dataset_{prefixo}_{ano}.csv",
        diretorio / f"{ano}.csv",
        diretorio / str(ano) / f"{ano}.csv",
        diretorio / f"dataset_{prefixo}_{ano}.csv",
    )
    for candidato in candidatos:
        if candidato.is_file():
            return candidato

    caminhos = ", ".join(str(candidato) for candidato in candidatos)
    raise FileNotFoundError(
        f"CSV de {prefixo} para {ano} não encontrado. Caminhos verificados: {caminhos}"
    )


def _ler_csv(arquivo: Path, ano: int) -> pd.DataFrame:
    dados = pd.read_csv(
        arquivo,
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    dados.columns = [str(coluna).strip() for coluna in dados.columns]

    colunas_url = [coluna for coluna in dados.columns if coluna.casefold() == "url"]
    if len(colunas_url) != 1:
        raise ValueError(f"{arquivo} precisa conter exatamente uma coluna URL.")
    if colunas_url[0] != "URL":
        dados = dados.rename(columns={colunas_url[0]: "URL"})

    colunas_ano = [coluna for coluna in dados.columns if coluna.casefold() == "ano"]
    if len(colunas_ano) > 1:
        raise ValueError(f"{arquivo} contém mais de uma coluna de ano.")
    if colunas_ano and colunas_ano[0] != "ano":
        dados = dados.rename(columns={colunas_ano[0]: "ano"})

    if "ano" in dados.columns:
        anos_no_arquivo = set(dados["ano"].astype(str).str.strip())
        if anos_no_arquivo != {str(ano)}:
            raise ValueError(
                f"{arquivo} deveria conter apenas ano={ano}, mas contém "
                f"{sorted(anos_no_arquivo)}."
            )
    else:
        dados["ano"] = str(ano)

    if dados.empty:
        raise ValueError(f"{arquivo} não contém registros.")
    if dados["URL"].astype(str).str.strip().eq("").any():
        raise ValueError(f"{arquivo} contém URL vazia.")

    dados["ano"] = ano
    return dados.drop(columns=list(COLUNAS_DE_ROTULO & set(dados.columns)))


def _aplicar_rotulo(dados: pd.DataFrame, label: int, classificacao: str) -> pd.DataFrame:
    resultado = dados.copy()
    resultado["label"] = label
    resultado["classificacao"] = classificacao
    return resultado


def _salvar_parquet(
    conexao: duckdb.DuckDBPyConnection,
    dados: pd.DataFrame,
    arquivo_saida: Path,
    nome_relacao: str,
) -> None:
    caminho_sql = str(arquivo_saida).replace("'", "''")
    conexao.register(nome_relacao, dados)
    try:
        conexao.execute(
            f"COPY {nome_relacao} TO '{caminho_sql}' (FORMAT PARQUET)"
        )
    finally:
        conexao.unregister(nome_relacao)


def montar_datasets(
    diretorio_phishing: str | Path,
    diretorio_legitimo: str | Path,
    diretorio_saida: str | Path,
    seed: int = SEED_PADRAO,
) -> None:
    diretorio_phishing = Path(diretorio_phishing).resolve()
    diretorio_legitimo = Path(diretorio_legitimo).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()
    diretorio_saida.mkdir(parents=True, exist_ok=True)

    datasets_anuais = []
    conexao = duckdb.connect(database=":memory:")
    for ano in ANOS:
        arquivo_phishing = _localizar_arquivo_csv(
            diretorio_phishing, ano, "phishing"
        )
        arquivo_legitimo = _localizar_arquivo_csv(
            diretorio_legitimo, ano, "legitimo"
        )

        phishing = _aplicar_rotulo(_ler_csv(arquivo_phishing, ano), 1, "phishing")
        legitimo = _aplicar_rotulo(_ler_csv(arquivo_legitimo, ano), 0, "legitimo")

        colunas_phishing = set(phishing.columns)
        colunas_legitimo = set(legitimo.columns)
        if colunas_phishing != colunas_legitimo:
            raise ValueError(
                f"As colunas dos CSVs de {ano} não coincidem. "
                f"Somente no phishing: {sorted(colunas_phishing - colunas_legitimo)}. "
                f"Somente no legítimo: {sorted(colunas_legitimo - colunas_phishing)}."
            )
        legitimo = legitimo[phishing.columns]

        combinado = pd.concat([phishing, legitimo], ignore_index=True)
        combinado = combinado.sample(
            frac=1,
            random_state=seed + ano,
        ).reset_index(drop=True)

        arquivo_saida = diretorio_saida / f"{ano}.parquet"
        _salvar_parquet(conexao, combinado, arquivo_saida, "dataset_anual")
        datasets_anuais.append(combinado)
        print(
            f"{ano}: phishing={len(phishing)}, legítimo={len(legitimo)}, "
            f"total={len(combinado)} -> {arquivo_saida}"
        )

    dataset_total = pd.concat(datasets_anuais, ignore_index=True)
    duplicatas_url = int(dataset_total.duplicated("URL").sum())
    conflitos_rotulo = int(
        (dataset_total.groupby("URL")["label"].nunique() > 1).sum()
    )
    print(f"URLs repetidas: {duplicatas_url}")
    print(f"URLs presentes nas duas classes: {conflitos_rotulo}")

    arquivo_total = diretorio_saida / "dataset_total.parquet"
    _salvar_parquet(conexao, dataset_total, arquivo_total, "dataset_completo")
    conexao.close()
    print(f"Total: {len(dataset_total)} registros -> {arquivo_total}")


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Combina os CSVs anuais de phishing e legítimos e grava arquivos Parquet."
        )
    )
    parser.add_argument(
        "--phishing",
        type=Path,
        default=raiz_projeto / "JPCERT" / "URL_Phishing_Contadas",
        help="Pasta com os CSVs anuais de phishing.",
    )
    parser.add_argument(
        "--legitimo",
        type=Path,
        default=raiz_projeto / "Common Crawl" / "dataset_commoncrawl_limpo",
        help="Pasta com os CSVs anuais legítimos.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=raiz_projeto / "dataset_total",
        help="Pasta onde os arquivos Parquet serão gravados.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED_PADRAO,
        help="Seed fixa do embaralhamento (padrão: 42).",
    )
    argumentos = parser.parse_args()

    montar_datasets(
        argumentos.phishing,
        argumentos.legitimo,
        argumentos.saida,
        argumentos.seed,
    )


if __name__ == "__main__":
    main()
