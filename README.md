# Extrator de dados clínicos (local, offline, gratuito)

Extrai variáveis de **relatórios de anatomia patológica**, **análises clínicas** e **relatórios operatórios** (PDF) e preenche a base de dados Excel, doente a doente, com **revisão humana obrigatória** antes de escrever.

Tudo corre no próprio computador: Python + [Ollama](https://ollama.com) (LLM local). Nenhum dado sai da máquina — o cliente LLM recusa qualquer URL que não seja `localhost`, e a interface gráfica só aceita ligações locais e não envia estatísticas.

## Arquitetura

```
PDFs por doente (entrada/<patient_id>/*.pdf)
        │
        ▼
1. Texto ─ pdfplumber → pdftotext → OCR (ocrmypdf, só se o PDF for imagem)
        │
        ▼
2. Classificação ─ nome do ficheiro (AP_, analises_, cirurgia_) ou palavras-chave
   + verificação de identidade: todos os PDFs da pasta têm o mesmo nº de processo?
     (e, se existir dados/correspondencia.csv, é o processo deste patient_id?)
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

## Estudo configurado: hemicolectomia direita por neoplasia do cólon

`config/campos.yaml` está preparado para a folha **DOENTES** da base `Base_HemicolectomiaDta`. Os campos têm o mesmo nome das colunas e os valores são convertidos para os códigos do DICIONARIO (`config/base_dados.exemplo.yaml`).

**Preenchidas automaticamente (68 variáveis)**
- Relatório operatório: date_surgery, operation_type, surgical_intent, emergency_surgery, asa, approach, conversion, obstruction, perforation, peritumoral_abscess, multivisceral_resection, additional_organ_resected, operative_time_min, blood_loss_ml, perioperative_transfusion, tumor_perforation_intraop, tumor_spillage, peritoneal_implants_intraop, ascites_intraop, anastomosis, intracorporeal_anastomosis, metastasectomy_same_operation, surgical_r_status.
- Anatomia patológica: tumor_location, tumor_size_mm, histology, mucinous_component, mucinous_percent, signet_ring_component, grade, tnm_prefix, t_stage, organ_invaded_t4b, n_stage, nodes_examined, nodes_positive, tumor_deposits, n_tumor_deposits, lymphatic_invasion, venous_invasion, perineural_invasion, tumor_budding, pathological_r_status, positive_margin, tumor_regression_grade, pathological_stage, pathological_perforation, mmr_status, mlh1, pms2, msh2, msh6, msi_status, kras, nras, braf.
- Análises (com conversão de unidades para as da BD: PCR mg/dL→mg/L, albumina g/dL→g/L): hb, wbc, neutrophils, lymphocytes, platelets, crp, albumin, creatinine, cea, ca199 (pré-op) e cea/ca199 no último follow-up.

**Regras do estudo**
- `conversion` = 8 se a abordagem for aberta; `intracorporeal_anastomosis` = 8 se aberta ou sem anastomose; `tumor_regression_grade` = 0 se prefixo `p` (sem neoadjuvância).
- `approach` é a via inicial; uma laparoscopia convertida fica `approach = 2` + `conversion = 1`.
- `pathological_stage` é calculado de pT/pN (assume M0 se pM não referido → vai sempre para revisão).
- `positive_margin` é calculado do R; `mmr_status` das 4 proteínas se não estiver escrito explicitamente.
- Valores sem código na BD **não são escritos** e ficam no registo para decisão manual: grau "baixo/alto grau" (a BD só tem G1–G4), pT4 sem a/b, pN1/pN2 sem subcategoria, Tis/T0, localização fora do cólon direito.

**Não cobertas** (precisam de outros documentos): identificação/demografia, comorbilidades, estadiamento clínico e neoadjuvância, complicações pós-operatórias, adjuvância, recorrência e follow-up. `anastomosis_location` e `emvi` estão no DICIONARIO mas não existem como colunas na folha DOENTES.

## Utilização

### 1. Organizar os PDFs
```
entrada/
  HCD001/                  ← patient_id (= coluna patient_id da BD)
    AP_peca_operatoria.pdf
    AP_biopsia.pdf
    analises_preop.pdf
    analises_pos_op.pdf
    cirurgia.pdf
  HCD002/
    ...
```
Prefixos no nome (`AP`, `analises`, `cirurgia`) tornam a classificação certa; sem eles a app usa palavras-chave do texto.

**Identidade.** A BD não pode ter o nº de processo, mas os PDFs têm. A app avisa se os PDFs de uma pasta tiverem números de processo diferentes. Para verificar também que são do doente certo, criar `dados/correspondencia.csv` (fica só no computador, fora do git):
```
patient_id;processo
HCD001;1234567
HCD002;7654321
```

### 2. Configurar a ligação à BD
O `instalar.bat` cria `config/base_dados.yaml`. Pôr o Excel em `dados/Base_Hemicolectomia.xlsx` (ou alterar `ficheiro:`).

### 3. Correr

**Interface gráfica**: `iniciar.bat` (ou `streamlit run app.py`)
Abre em http://localhost:8501 com três separadores: Extrair → Rever → Preencher BD.

**Linha de comandos**
```
python -m extrator extrair entrada/                 # gera resultados/revisao_*.xlsx
python -m extrator preencher resultados/revisao_X.xlsx --simular
python -m extrator preencher resultados/revisao_X.xlsx
python -m extrator testar entrada/HCD001/AP.pdf --texto   # ver o que é extraído de 1 ficheiro
```

## Adicionar ou alterar variáveis

Tudo em `config/campos.yaml`, sem programar. Exemplo: se criar a coluna `emvi` na BD,
```yaml
  - nome: emvi                         # = nome da coluna
    descricao: Invasão venosa extramural presente?
    tipo: booleano
    documento: anatomia_patologica
    metodo: hibrido
    padrao: 'invas[ãa]o venosa extramural\s*:?\s*(presente|ausente)'   # 1.º grupo = valor
    mapa: {presente: sim, ausente: nao}
```
- Só LLM: `metodo: llm` e uma `descricao` precisa.
- Códigos: acrescentar em `recodificacao` de `config/base_dados.yaml` (booleanos já usam 1/0).
- Nova análise: acrescentar em `analises:` com `aliases`, `unidade`, `conversoes`, `regra` e `janela_dias`.

## Segurança e proteção de dados

- PDFs, Excel, `dados/` e resultados estão no `.gitignore` — **nunca** fazer commit de dados de doentes.
- O nº de processo é usado só para verificação e nunca é escrito nos resultados nem na BD.
- LLM só em `localhost`; interface só em `localhost`; telemetria do Streamlit desligada.
- A BD é copiada (`*.backup_AAAAMMDD_HHMMSS.xlsx`) antes de cada escrita, e cada alteração fica em `registo_preenchimento_*.xlsx`.
- Recomendado: disco cifrado (BitLocker/FileVault) e confirmar com a Comissão de Ética / EPD do hospital que o uso de dados está coberto pelo protocolo do estudo.

## Limitações atuais

- As regex foram afinadas com **relatórios fictícios** (`tests/fixtures/`). É preciso calibrá-las com relatórios reais anonimizados do hospital.
- Análises: o formato dos relatórios do laboratório ainda não foi visto; aliases e unidades podem precisar de ajuste.
- A integração com o Ollama está testada só com respostas simuladas; falta validá-la com o modelo real.
- Análises em tabela cumulativa (várias datas em colunas no mesmo PDF): não suportado. Cada PDF é tratado como uma colheita.
- Vários relatórios operatórios: é usado o mais antigo como cirurgia índice (com aviso).
- Várias peças de AP: é usada a que tem pTNM; biópsias ficam como secundárias.
- Campos sim/não que o relatório não menciona (ex.: spillage, transfusão) ficam vazios, não "0": a decisão entre 0 e 9 (desconhecido) é sua.
- Tumores síncronos: o regex apanha a primeira expressão pTNM completa; o LLM é instruído a escolher a lesão de maior estadio. Revisão manual necessária.
- A escrita em Excel usa `openpyxl`: formatação, validações e fórmulas são mantidas (verificado na BD do estudo), mas gráficos e imagens dentro do ficheiro podem perder-se. Há backup.
- Antes de escrever, cada código é verificado contra as listas de validação do Excel (ex.: "0,1,8,9"); códigos fora da lista não são escritos.
- Doentes novos vão para a primeira linha pré-numerada livre (a BD já tem N e fórmulas até à linha 1001).

## Testes
```
pytest -q
```
