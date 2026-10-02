# Dataset temporal de phishing

Este projeto monta um dataset de URLs legítimas e de phishing por ano e produz uma divisão temporal para treino, validação e teste. Os scripts ficam em `scripts/`, numerados na ordem recomendada. CSVs de entrada e Parquets gerados permanecem locais e são ignorados pelo Git.

## Requisitos

- Python 3.10 ou superior
- Acesso à internet para extrair os índices Common Crawl
- Arquivos de entrada descritos abaixo

Instale as dependências na raiz do projeto:

```powershell
py -m pip install -r requirements.txt
```

## Arquivos de entrada

### Domínios populares: Tranco

Baixe o arquivo [top-1m.csv.zip do Tranco](https://tranco-list.eu/top-1m.csv.zip), extraia `top-1m.csv` e coloque-o em `Dominio TrancoLIst/top-1m.csv`. O ranking contém posição e domínio, sem cabeçalho, formato esperado pelo script `01_extrair_top_dominios.py`.

O Tranco é um ranking de popularidade de domínios orientado a pesquisa, que combina listas de diferentes provedores. Como a lista é atualizada diariamente, registre a data ou o ID permanente da versão usada para tornar a extração reproduzível. Consulte [Retrieve latest list](https://tranco-list.eu/latest_list) para obter os detalhes e o ID da versão.

### URLs de phishing: JPCERT/CC

Os CSVs mensais usados neste projeto vêm do repositório oficial [JPCERTCC/phishurl-list](https://github.com/JPCERTCC/phishurl-list). Para baixar os arquivos, abra o repositório e use **Code > Download ZIP**, ou clone o repositório. Copie para `JPCERT/` os CSVs mensais dos anos desejados, mantendo os nomes `YYYYMM.csv`. O script espera as colunas `date` e `URL`; a coluna `description`, quando presente, descreve a marca imitada. O ano de cada URL é obtido da coluna `date`, não do nome do arquivo.

JPCERT/CC significa **Japan Computer Emergency Response Team Coordination Center**. É uma organização independente japonesa sem fins lucrativos e um CSIRT (equipe de resposta a incidentes de segurança computacional). Ela coordena o tratamento e o compartilhamento de informações sobre incidentes com provedores de rede, fornecedores de segurança, órgãos públicos, associações da indústria e equipes parceiras. A lista citada é publicada pelo projeto JPCERT/CC; seus registros não significam necessariamente que a URL ainda esteja ativa.

### URLs legítimas: Common Crawl

Não é necessário baixar os enormes arquivos WARC completos. O script `02_extrair_commoncrawl.py` consulta o [índice colunar do Common Crawl](https://data.commoncrawl.org/cc-index/table/cc-main/index.html), hospedado em [data.commoncrawl.org](https://data.commoncrawl.org/), e seleciona URLs dos crawls anuais configurados no código. O Common Crawl é um arquivo aberto de páginas da Web mantido por uma organização sem fins lucrativos; os dados e índices estão disponíveis para acesso público via HTTP(S). Veja o [guia oficial de acesso aos dados](https://commoncrawl.org/get-started).

As URLs legítimas deste projeto são filtradas pelos domínios Tranco, por resposta HTTP 200 e pelo domínio registrado indicado pelo índice. Isso é um critério operacional do dataset, não uma certificação independente de que cada página seja benigna.

Os CSVs de origem, CSVs intermediários e Parquets intermediários (`dataset_total/`) são arquivos locais e não precisam ser enviados ao GitHub. Os três Parquets finais de `DATASET/` podem ser incluídos como artefatos prontos para uso, desde que a redistribuição esteja de acordo com os termos das fontes. O `.gitignore` deve manter os dados intermediários fora do repositório e permitir esses três arquivos finais.

## Ordem de execução

Execute os comandos a partir da raiz do projeto. No Windows, `py` pode ser usado no lugar de `python`.

1. Extrair os 10 mil domínios do ranking Tranco:

   ```powershell
   py scripts/01_extrair_top_dominios.py
   ```

   Lê `Dominio TrancoLIst/top-1m.csv` e cria `Common Crawl/top_10k_dominios.csv`. Se esse arquivo já estiver preparado, esta etapa pode ser pulada.

2. Extrair URLs legítimas dos crawls anuais Common Crawl (usa o ranking Tranco criado na etapa anterior):

   ```powershell
   py scripts/02_extrair_commoncrawl.py --anos 2019 2020 2021 2022 2023 2024 2025
   ```

   Acessa os índices públicos Common Crawl e cria `Common Crawl/2019/dataset_legitimo_2019.csv` até `Common Crawl/2025/dataset_legitimo_2025.csv`. Pode levar tempo e requer internet. A lista padrão usa um crawl representativo por ano, definido em `02_extrair_commoncrawl.py`.

3. Validar, normalizar e deduplicar os CSVs legítimos por ano:

   ```powershell
   py scripts/03_limpar_legitimos.py --deduplicacao ano
   ```

   Cria `Common Crawl/dataset_commoncrawl_limpo/` e o relatório `Common Crawl/dataset_commoncrawl_rejeitadas.csv`. A deduplicação por ano preserva reaparições de uma URL em anos diferentes.

4. Limpar e organizar os registros mensais de phishing JPCERT:

   ```powershell
   py scripts/04_gerar_phishing_jpcert.py --deduplicacao ano
   ```

   Cria `JPCERT/dataset_phishing/`, com um CSV por ano, e `JPCERT/dataset_phishing/jpcert_rejeitadas.csv`. Registros inválidos e arquivos fora do formato esperado são informados no relatório.

5. Selecionar phishing para uma proporção aproximada de 80:20 por ano:

   ```powershell
   py scripts/05_selecionar_phishing.py --seed 42
   ```

   Combina a quantidade necessária de phishing com o volume de legítimas e grava `JPCERT/URL_Phishing_Contadas/`, além do relatório `proporcao_80_20.csv`.

6. Combinar as classes e criar os Parquets anuais e total:

   ```powershell
   py scripts/06_montar_dataset.py --seed 42
   ```

   Grava `dataset_total/2019.parquet` até `dataset_total/2025.parquet` e `dataset_total/dataset_total.parquet`. Cada registro recebe `label` (1 phishing, 0 legítimo) e `classificacao` (`phishing` ou `legitimo`).

7. Verificar esquema, contagens, vazios, rótulos e duplicatas dos Parquets anuais e do total:

   ```powershell
   py scripts/07_verificar_dataset.py
   ```

   Lê `dataset_total/` e imprime um relatório para cada Parquet anual e para o total.

8. Procurar credenciais expostas e remover as URLs que as contêm:

   ```powershell
   py scripts/08_sanitizar_credenciais.py
   ```

   Examina as colunas textuais dos Parquets em `dataset_total/` com DuckDB, remove linhas que correspondam a padrões de chaves, tokens, senhas, URLs de conexão com credenciais, JWTs, chaves privadas e webhooks. Os originais não são alterados. Cria cópias em `dataset_total_sanitizado/` e o relatório `dataset_total_sanitizado/relatorio_remocao_credenciais.csv`, com contagens por arquivo e categoria, sem incluir os valores encontrados. Os padrões são heurísticos e podem exigir revisão antes de publicar os resultados.

9. Remover rótulos ambíguos, deduplicar globalmente e fazer o time split:

   ```powershell
   py scripts/09_dividir_dataset.py --seed 42
   ```

   Por padrão, lê `dataset_total_sanitizado/dataset_total.parquet`. O split usa `train` = 2019–2022, `val` = 2023 e `test` = 2024–2025. Grava cada arquivo dentro da pasta correspondente: `DATASET/train/train.parquet`, `DATASET/val/val.parquet` e `DATASET/test/test.parquet`. O script imprime as contagens e encerra com erro se as verificações de anos, rótulos, URLs ou interseções falharem. Os caminhos podem ser sobrescritos com `--entrada` e `--saida-dir`.

## Por que não há script de backup?

Cada etapa gera uma representação processada do mesmo conjunto-base: dados extraídos, dados limpos e amostrados, Parquets anuais e total, versão sanitizada e, por fim, os splits temporais. Esses arquivos ficam em etapas e pastas diferentes, então um script que apenas copiasse cada saída criaria duplicatas sem acrescentar uma nova etapa de processamento.

## Fontes e referências

- [JPCERT/CC Phishing URL dataset](https://github.com/JPCERTCC/phishurl-list/) — fonte das URLs rotuladas como phishing.
- [Sobre o JPCERT/CC](https://www.jpcert.or.jp/english/about/) — descrição institucional da organização.
- [Tranco](https://tranco-list.eu/) — ranking de domínios usado para selecionar sites legítimos.
- [Download do top 1 milhão Tranco](https://tranco-list.eu/top-1m.csv.zip) — arquivo compactado atualizado; registre a data/ID da versão utilizada.
- [Common Crawl: dados e índices](https://data.commoncrawl.org/) e [guia de acesso](https://commoncrawl.org/get-started) — fonte dos crawls usados para a classe legítima.

As fontes mantêm seus próprios termos e condições. Consulte-os antes de redistribuir dados de origem. URLs de phishing podem ser perigosas: não as abra diretamente em um navegador ou ambiente sem isolamento.

## Scripts

| Script | Função |
| --- | --- |
| `01_extrair_top_dominios.py` | Seleciona os 10 mil primeiros domínios do ranking Tranco. |
| `02_extrair_commoncrawl.py` | Extrai os CSVs anuais de URLs legítimas do Common Crawl. |
| `03_limpar_legitimos.py` | Valida e normaliza URLs legítimas, com deduplicação por ano. |
| `04_gerar_phishing_jpcert.py` | Valida e organiza os CSVs mensais de phishing por ano. |
| `05_selecionar_phishing.py` | Amostra phishing por ano para proporção 80:20. |
| `06_montar_dataset.py` | Adiciona rótulos e grava Parquets anuais e total. |
| `07_verificar_dataset.py` | Inspeciona os Parquets do dataset total. |
| `08_sanitizar_credenciais.py` | Remove registros com padrões de credenciais expostas e informa quantidades. |
| `09_dividir_dataset.py` | Aplica a limpeza final e cria train/val/test temporal. |

O `01_extrair_top_dominios.py` pode ser pulado se a lista Tranco já estiver pronta. A ordem final é: verificar o dataset original (07), sanitizar (08) e dividir o total sanitizado (09). Se a sanitização não for executada, passe explicitamente `--entrada dataset_total/dataset_total.parquet` ao divisor.
