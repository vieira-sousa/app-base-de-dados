# Extrator de dados clínicos (local, offline, gratuito)

Extrai variáveis de **relatórios de anatomia patológica**, **análises clínicas** e **relatórios operatórios** (PDF) e preenche a base de dados Excel, doente a doente, com **revisão humana obrigatória** antes de escrever.

Tudo corre no próprio computador: Python + [Ollama](https://ollama.com) (LLM local). Nenhum dado sai da máquina — o cliente LLM recusa qualquer URL que não seja `localhost`, e a interface gráfica só aceita ligações locais e não envia estatísticas.

## Arquitetura

```
PDFs por doente (entrada/<nº processo>/*.pdf)
        │
        ▼
1. Texto ─ pdfplumber → pdftotext → OCR (ocrmypdf, só se o PDF for imagem)
        │
        ▼
2. Classificação ─ nome do ficheiro (AP_, analises_, cirurgia_) ou palavras-chave
   + verificação: nº processo no PDF = nome da pasta? (aviso se não)
        │
        ▼
3. Motor híbrido (por campo, configurável em config/campos.yaml)
   ├─ Regex ─ pTNM, gânglios x/y, R0/R1/R2, CRM, L/V/Pn, TRG, perdas, duração, abordagem, estoma…
   ├─ LLM local (Ollama, JSON com esquema obrigatório) ─ histologia, procedimento, MMR, intercorrências…
   │     └─ cada valor do LLM traz uma citação; se a citação não existir no PDF → "rever" (anti-alucinação)
   └─ Análises ─ regex por parâmetro + regra temporal relativa à data da cirurgia
         (último pré-op, máximo pós-op em N dias, …)
        │
        ▼
4. Fusão e discordâncias
   regex = LLM → ok (verde) │ regex ≠ LLM → rever (amarelo) │ nada → sem dados (cinzento)
   vários documentos com valores diferentes → rever
        │
        ▼
5. Revisão ─ revisao_AAAAMMDD.xlsx (ou interface gráfica): valor + evidência (excerto do PDF) + ficheiro
   Corrigir se necessário e marcar aprovar = S
        │
        ▼
6. Preenchimento da BD Excel
   backup automático · só valores aprovados · nunca sobrescreve células preenchidas (regista conflito)
   recodificação (ex.: laparoscópica → 1) · registo de todas as alterações
```

Porquê híbrido: as regex são determinísticas e exatas para formatos padronizados (pTNM, números); o LLM lida com texto livre. Onde ambos se aplicam, a concordância serve de controlo de qualidade. Valores numéricos de análises são extraídos só por regex, porque é aí que os modelos pequenos mais erram.

## Instalação

1. **Python 3.11+** (Windows: python.org, marcar "Add to PATH").
2. Na pasta do projeto:
   ```
   python -m venv .venv
   .venv\Scripts\activate        (Windows)   |   source .venv/bin/activate   (Mac/Linux)
   pip install -r requirements.txt
   ```
3. **Ollama** (opcional, mas recomendado): instalar de ollama.com e depois
   ```
   ollama pull qwen2.5:7b
   ```
   ~5 GB de disco; precisa de ~8 GB de RAM. Corre em CPU (≈1–3 min por relatório) ou muito mais rápido com GPU. Depois de descarregado funciona sem internet. Sem Ollama, a app funciona só com regex (`--sem-llm`).
4. **OCR** (só para PDFs digitalizados/fotografados): `ocrmypdf` + `tesseract-ocr-por`.
5. Verificar: `python -m extrator verificar`

## Utilização

### 1. Organizar os PDFs
```
entrada/
  1234567/                 ← nº de processo (= valor da coluna ID na BD)
    AP_peca_operatoria.pdf
    AP_biopsia.pdf
    analises_2025-03-10.pdf
    analises_pos_op.pdf
    cirurgia.pdf
  7654321/
    ...
```
Prefixos no nome (`AP`, `analises`, `cirurgia`) tornam a classificação certa; sem eles a app usa palavras-chave do texto.

### 2. Configurar a ligação à sua BD
Copiar `config/base_dados.exemplo.yaml` para `config/base_dados.yaml` e ajustar: ficheiro, folha, coluna de ID, nomes das colunas e recodificações.

### 3. Correr

**Interface gráfica**
```
streamlit run app.py
```
Abre em http://localhost:8501 com três separadores: Extrair → Rever → Preencher BD.

**Linha de comandos**
```
python -m extrator extrair entrada/                 # gera resultados/revisao_*.xlsx
python -m extrator preencher resultados/revisao_X.xlsx --simular
python -m extrator preencher resultados/revisao_X.xlsx
python -m extrator testar entrada/1234567/AP.pdf --texto   # ver o que é extraído de 1 ficheiro
```

## Adicionar ou alterar variáveis

Tudo em `config/campos.yaml`, sem programar:

```yaml
  - nome: budding_tumoral
    descricao: Grau de budding tumoral (Bd1, Bd2, Bd3)
    tipo: categoria
    opcoes: [Bd1, Bd2, Bd3]
    documento: anatomia_patologica
    metodo: hibrido
    padrao: '\b(Bd\s?[123])\b'          # regex própria: 1.º grupo = valor
```
- Só LLM: `metodo: llm` e uma `descricao` precisa.
- Nova análise: acrescentar em `analises:` com `aliases`, `regra` e `janela_dias`.

## Segurança e proteção de dados

- PDFs, Excel e resultados estão no `.gitignore` — **nunca** fazer commit de dados de doentes.
- LLM só em `localhost`; interface só em `localhost`; telemetria do Streamlit desligada.
- A BD é copiada (`*.backup_AAAAMMDD_HHMMSS.xlsx`) antes de cada escrita, e cada alteração fica em `registo_preenchimento_*.xlsx`.
- Recomendado: disco cifrado (BitLocker/FileVault) e confirmar com a Comissão de Ética / EPD do hospital que o uso de dados está coberto pelo protocolo do estudo.

## Limitações atuais

- As regex foram afinadas com **relatórios fictícios** (`tests/fixtures/`). É preciso calibrá-las com relatórios reais anonimizados do hospital.
- A integração com o Ollama está testada só com respostas simuladas; falta validá-la com o modelo real.
- Análises em tabela cumulativa (várias datas em colunas no mesmo PDF): não suportado. Cada PDF é tratado como uma colheita.
- Vários relatórios operatórios: é usado o mais antigo como cirurgia índice (com aviso).
- Várias peças de AP: é usada a que tem pTNM; biópsias ficam como secundárias.
- Tumores síncronos: o regex apanha a primeira expressão pTNM completa; o LLM é instruído a escolher a lesão de maior estadio. Revisão manual necessária.
- A escrita em Excel usa `openpyxl`: formatação, validações e fórmulas são mantidas, mas gráficos e imagens dentro do ficheiro da BD podem perder-se. Há backup.

## Testes
```
pytest -q
```
