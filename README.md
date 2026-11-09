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

## Instalação (Windows 10)

1. **Python 3.12**: https://www.python.org/downloads/windows/ — marcar *"Add python.exe to PATH"*.
2. **Ollama**: https://ollama.com/download/windows (fica a correr na barra de tarefas).
3. Descarregar este projeto e fazer **duplo clique em `instalar.bat`**: cria o ambiente, instala as dependências, descarrega o modelo `qwen2.5:3b` (~1,9 GB) e verifica tudo. Só este passo precisa de internet.
4. Usar: **duplo clique em `iniciar.bat`** (abre o browser em http://localhost:8501).

Mac/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && ollama pull qwen2.5:3b`.

### Escolha do modelo para o seu hardware

Configurado em `config/llm.yaml` para **8 GB RAM / i5 / GeForce 940MX 2 GB**:

| Modelo | Disco/RAM | Notas |
|---|---|---|
| `qwen2.5:3b` (padrão) | ~1,9 GB | bom em JSON e português; ponto de partida |
| `llama3.2:3b` | ~2,0 GB | alternativa |
| `gemma3:4b` | ~3,3 GB | possivelmente mais preciso, mais lento |
| `qwen2.5:7b` | ~4,7 GB | só com 16 GB de RAM |

- Com 8 GB, modelos 7-8B obrigam o Windows a usar disco como memória e ficam muito lentos.
- A 940MX (2 GB) acelera pouco. Se o Ollama der erros de GPU/CUDA, pôr `num_gpu: 0` em `config/llm.yaml`.
- Comparar modelos na sua máquina: `testar_llm.bat` ou `testar_llm.bat gemma3:4b` (mostra tempo por relatório e acerto num relatório fictício).
- O tempo em CPU é da ordem de minutos por relatório. Para muitos doentes: deixar a correr à noite, com o portátil ligado à corrente e fechar o browser e outros programas. Para testes rápidos: `llm_em_hibridos: se_regex_falhar` (o LLM só trata o que a regex não encontrou).
- OCR (PDFs digitalizados) no Windows exige instalar Tesseract e Ghostscript; só é necessário se os PDFs não tiverem texto selecionável.

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

**Interface gráfica**: `iniciar.bat` (ou `streamlit run app.py`)
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
