import argparse
import csv
import random
from pathlib import Path


CAMPOS_RELATORIO = [
    "ano",
    "urls_benignas",
    "phishing_disponiveis",
    "phishing_selecionadas",
    "proporcao_phishing_percentual",
    "seed",
]
CAMPOS_PHISHING = ["ano", "URL", "URL_não_normalizada"]


def _arquivos_por_ano(
    diretorio: Path,
    padrao: str,
    prefixo: str,
    sufixo: str,
) -> dict[str, Path]:
    arquivos: dict[str, Path] = {}
    for arquivo in diretorio.glob(padrao):
        ano = arquivo.parent.name
        if (
            len(ano) == 4
            and ano.isdigit()
            and arquivo.name == f"{prefixo}{ano}{sufixo}"
        ):
            arquivos[ano] = arquivo
    return arquivos


def _ler_csv(
    arquivo: Path,
    colunas_esperadas: set[str],
) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with arquivo.open("r", newline="", encoding="utf-8-sig") as entrada:
            leitor = csv.DictReader(entrada, strict=True)
            colunas = leitor.fieldnames or []
            if not colunas_esperadas.issubset(colunas):
                faltantes = sorted(colunas_esperadas.difference(colunas))
                raise ValueError(
                    f"colunas ausentes em '{arquivo}': {', '.join(faltantes)}"
                )
            registros = list(leitor)
    except (OSError, UnicodeError, csv.Error) as erro:
        raise RuntimeError(f"Falha ao ler '{arquivo}': {erro}") from erro

    for numero_linha, registro in enumerate(registros, start=2):
        if None in registro:
            raise ValueError(
                f"Registro com campos extras em '{arquivo}', linha {numero_linha}"
            )
        faltantes = [coluna for coluna in colunas_esperadas if registro.get(coluna) is None]
        if faltantes:
            raise ValueError(
                f"Registro incompleto em '{arquivo}', linha {numero_linha}: "
                f"{', '.join(sorted(faltantes))}"
            )
    return registros, colunas


def selecionar_urls_phishing(
    diretorio_phishing: str | Path,
    diretorio_commoncrawl: str | Path,
    diretorio_saida: str | Path,
    seed: int = 42,
) -> dict[str, tuple[int, int, int]]:
    diretorio_phishing = Path(diretorio_phishing).resolve()
    diretorio_commoncrawl = Path(diretorio_commoncrawl).resolve()
    diretorio_saida = Path(diretorio_saida).resolve()

    arquivos_phishing = _arquivos_por_ano(
        diretorio_phishing, "*/dataset_phishing_*.csv", "dataset_phishing_", ".csv"
    )
    arquivos_benignos = _arquivos_por_ano(
        diretorio_commoncrawl, "*/dataset_legitimo_*.csv", "dataset_legitimo_", ".csv"
    )
    if not arquivos_phishing:
        raise FileNotFoundError(
            f"Nenhum dataset anual de phishing encontrado em {diretorio_phishing}"
        )
    if not arquivos_benignos:
        raise FileNotFoundError(
            f"Nenhum dataset anual Common Crawl encontrado em {diretorio_commoncrawl}"
        )
    if set(arquivos_phishing) != set(arquivos_benignos):
        raise ValueError(
            "Os anos dos datasets de phishing e Common Crawl não coincidem. "
            f"Phishing: {sorted(arquivos_phishing)}; "
            f"Common Crawl: {sorted(arquivos_benignos)}"
        )

    diretorio_saida.mkdir(parents=True, exist_ok=True)
    relatorio: list[dict[str, str | int]] = []
    resultado: dict[str, tuple[int, int, int]] = {}

    for ano in sorted(arquivos_phishing):
        phishing, colunas_phishing = _ler_csv(
            arquivos_phishing[ano], set(CAMPOS_PHISHING)
        )
        benignos, _ = _ler_csv(arquivos_benignos[ano], {"URL"})
        if not benignos:
            raise ValueError(f"Não há URLs Common Crawl no ano {ano}")

        quantidade_alvo = (len(benignos) + 3) // 4
        if len(phishing) < quantidade_alvo:
            raise ValueError(
                f"Ano {ano}: existem {len(phishing)} URLs de phishing, "
                f"mas são necessárias {quantidade_alvo} para a proporção 80:20 "
                f"com {len(benignos)} URLs Common Crawl."
            )

        gerador = random.Random(f"{seed}:{ano}")
        selecionadas = gerador.sample(phishing, quantidade_alvo)
        ano_saida = diretorio_saida / ano
        ano_saida.mkdir(parents=True, exist_ok=True)
        arquivo_saida = ano_saida / f"dataset_phishing_{ano}.csv"
        with arquivo_saida.open("w", newline="", encoding="utf-8") as saida:
            escritor = csv.DictWriter(saida, fieldnames=colunas_phishing)
            escritor.writeheader()
            escritor.writerows(selecionadas)

        total = len(benignos) + quantidade_alvo
        proporcao = quantidade_alvo / total * 100
        relatorio.append(
            {
                "ano": ano,
                "urls_benignas": len(benignos),
                "phishing_disponiveis": len(phishing),
                "phishing_selecionadas": quantidade_alvo,
                "proporcao_phishing_percentual": f"{proporcao:.4f}",
                "seed": seed,
            }
        )
        resultado[ano] = (len(benignos), len(phishing), quantidade_alvo)
        print(
            f"{ano}: benignas={len(benignos)}, phishing disponíveis={len(phishing)}, "
            f"selecionadas={quantidade_alvo} ({proporcao:.2f}% phishing)."
        )

    arquivo_relatorio = diretorio_saida / "proporcao_80_20.csv"
    with arquivo_relatorio.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS_RELATORIO)
        escritor.writeheader()
        escritor.writerows(relatorio)

    print(f"Datasets de phishing selecionados: {diretorio_saida}")
    print(f"Relatório da proporção: {arquivo_relatorio}")
    return resultado


def main() -> None:
    raiz_projeto = Path(__file__).resolve().parents[1]
    diretorio_jpcert = raiz_projeto / "JPCERT"
    parser = argparse.ArgumentParser(
        description=(
            "Seleciona por ano a quantidade de URLs de phishing necessária "
            "para compor uma proporção aproximada de 80:20 com Common Crawl."
        )
    )
    parser.add_argument(
        "--phishing",
        type=Path,
        default=diretorio_jpcert / "dataset_phishing",
        help="Pasta com os CSVs anuais deduplicados de phishing.",
    )
    parser.add_argument(
        "--commoncrawl",
        type=Path,
        default=raiz_projeto / "Common Crawl" / "dataset_commoncrawl_limpo",
        help="Pasta com os CSVs anuais do Common Crawl limpo.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=diretorio_jpcert / "URL_Phishing_Contadas",
        help="Pasta de saída dos CSVs amostrados por ano.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed para que a seleção aleatória possa ser reproduzida.",
    )
    argumentos = parser.parse_args()
    selecionar_urls_phishing(
        argumentos.phishing,
        argumentos.commoncrawl,
        argumentos.saida,
        argumentos.seed,
    )


if __name__ == "__main__":
    main()
