import os
import uuid
import json
import time
import gspread
from flask import Flask, render_template, request, jsonify
from google.oauth2.service_account import Credentials
from datetime import datetime
import re
from flask import request, jsonify





app = Flask(__name__)

# Nome oficial da planilha
NOME_PLANILHA = 'contagem_semi_acabado'

# --- CACHE EM MEMÓRIA PARA A BUSCA RÁPIDA ---
CACHE_PRODUTOS = []
ULTIMA_ATUALIZACAO_CACHE = 0
TEMPO_CACHE_SEGUNDOS = 600  # Atualiza o cache a cada 10 minutos (600s)

def conectar_google_sheets():
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        if "GOOGLE_CREDENTIALS_JSON" in os.environ:
            creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        else:
            creds = Credentials.from_service_account_file('credenciais.json', scopes=scopes)
            
        gc = gspread.authorize(creds)
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao autenticar: {e}")
        return None

def obter_produtos_cached():
    """Carrega os produtos na memória do servidor e atualiza apenas a cada 10 minutos."""
    global CACHE_PRODUTOS, ULTIMA_ATUALIZACAO_CACHE
    agora = time.time()
    
    # Se o cache estiver válido e com dados, usa a memória instantaneamente
    if CACHE_PRODUTOS and (agora - ULTIMA_ATUALIZACAO_CACHE < TEMPO_CACHE_SEGUNDOS):
        return CACHE_PRODUTOS

    try:
        print("[CACHE] Carregando produtos do Google Sheets...")
        gc = conectar_google_sheets()
        if not gc:
            return CACHE_PRODUTOS  # Se falhar, devolve o cache antigo
            
        planilha = gc.open(NOME_PLANILHA)
        sheet = planilha.worksheet('dados')
        dados = sheet.get_all_values()

        novos_produtos = []
        if len(dados) >= 2:
            for linha in dados[1:]:
                if len(linha) >= 1:
                    codigo = str(linha[0]).strip()
                    descricao = str(linha[1]).strip() if len(linha) > 1 else ''
                    if codigo:
                        novos_produtos.append({
                            'codigo': codigo,
                            'descricao': descricao,
                            'codigo_lower': codigo.lower(),
                            'descricao_lower': descricao.lower()
                        })

        CACHE_PRODUTOS = novos_produtos
        ULTIMA_ATUALIZACAO_CACHE = agora
        print(f"[CACHE] Carregados {len(CACHE_PRODUTOS)} produtos com sucesso!")
    except Exception as e:
        print(f"[ERRO CARREGAR CACHE]: {e}")

    return CACHE_PRODUTOS

# --- ROTA PRINCIPAL DO SITE ---
@app.route('/')
def index_contagem():
    return render_template('contagem_estoque.html')

# --- ROTA DE AUTOCOMPLETE (ULTRA RÁPIDA VIA CACHE) ---
@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])

    produtos = obter_produtos_cached()
    resultados = []
    codigos_vistos = set()

    for p in produtos:
        if termo in p['codigo_lower'] or termo in p['descricao_lower']:
            cod_upper = p['codigo'].upper()
            if cod_upper not in codigos_vistos:
                codigos_vistos.add(cod_upper)
                resultados.append({'codigo': p['codigo'], 'descricao': p['descricao']})
                if len(resultados) >= 15:
                    break

    return jsonify(resultados)

# --- ROTA PARA FORÇAR ATUALIZAÇÃO DO CACHE (SE ALTERAR A PLANILHA) ---
@app.route('/atualizar_cache', methods=['GET'])
def atualizar_cache_manual():
    global ULTIMA_ATUALIZACAO_CACHE
    ULTIMA_ATUALIZACAO_CACHE = 0  # Força recarregar no próximo request
    obter_produtos_cached()
    return jsonify({"sucesso": True, "total_produtos": len(CACHE_PRODUTOS)})

# --- ROTA PARA SALVAR LOTE ---
# --- ROTA PARA SALVAR LOTE COM VALIDAÇÃO DE CÓDIGO ---
# --- ROTA PARA SALVAR LOTE NA SEQUÊNCIA CORRETA ---
@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data')
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        produtos = obter_produtos_cached()
        codigos_validos = {p['codigo'].upper() for p in produtos}

        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip().upper()
            if not cod_item:
                return jsonify({'sucesso': False, 'mensagem': 'Existe um item com código em branco!'})
            if cod_item not in codigos_validos:
                return jsonify({
                    'sucesso': False, 
                    'mensagem': f'O código "{cod_item}" não existe na aba "dados" da planilha!'
                })

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        
        try:
            sheet_mov = planilha.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            sheet_mov = planilha.add_worksheet(title=data_lote, rows="1000", cols="8")
            # Cabeçalho sem a coluna Status, mantendo apenas Inventario Feito
            sheet_mov.append_row(["ID", "Data", "Código", "Saldo Estoque", "Quantidade Contada", "Diferença", "Tipo", "Inventario Feito"])

        linhas_adicionar = []
        for item in itens:
            row_id = str(uuid.uuid4())[:8]
            linhas_adicionar.append([
                row_id,
                data_lote,
                item.get('CODIGO', ''),
                int(item.get('SALDO', 0)),
                int(item.get('QUANTIDADE', 1)),
                int(item.get('DIFERENCA', 0)),
                'Contagem',
                'Não' # Inventario Feito
            ])

        sheet_mov.append_rows(linhas_adicionar)
        return jsonify({'sucesso': True, 'mensagem': f'Gravado com sucesso na aba {data_lote}!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'})



@app.route('/consultar_movimentacao', methods=['GET'])
def consultar_movimentacao():
    try:
        data_consulta = request.args.get('data') # Ex: "10/2026"
        nome_aba_saldo = f"SALDO_{data_consulta.replace('/', '_')}"
        
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)

        # 1. Carregar o dicionário de saldos da aba SALDO_MM_YYYY para consulta rápida
        saldos_dict = {}
        try:
            aba_saldo = planilha.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:  # Pula o cabeçalho
                if len(linha) >= 2:
                    cod = str(linha[0]).strip().upper()
                    sld = int(linha[1]) if str(linha[1]).isdigit() or (str(linha[1]).startswith('-') and str(linha[1][1:]).isdigit()) else 0
                    saldos_dict[cod] = sld
        except Exception:
            pass # Se a aba de saldo ainda não existir, o saldo será 0

        # 2. Carregar os itens da movimentação da aba do mês
        try:
            sheet_mov = planilha.worksheet(data_consulta)
            registros = sheet_mov.get_all_records()
            
            itens_formatados = []
            updates_necessarios = []

            for idx, r in enumerate(registros, start=2): # start=2 porque a linha 1 é o cabeçalho
                codigo = str(r.get("Código", "")).strip()
                codigo_upper = codigo.upper()
                
                # Pega o saldo real diretamente da aba de saldo correspondente
                saldo_estoque = saldos_dict.get(codigo_upper, 0)
                
                qtd = r.get("Quantidade Contada", r.get("Quantidade", 0))
                qtd_int = int(qtd) if str(qtd).isdigit() or (str(qtd).startswith('-') and str(qtd)[1:].isdigit()) else 0
                
                # Recalcula a diferença correta (Quantidade Contada - Saldo de Estoque)
                diferenca = qtd_int - saldo_estoque

                itens_formatados.append({
                    "ID": str(r.get("ID", "")),
                    "Data": str(r.get("Data", "")),
                    "Código": codigo,
                    "SaldoEstoque": saldo_estoque,
                    "Quantidade": qtd_int,
                    "Diferenca": diferenca,
                    "Tipo": str(r.get("Tipo", "Contagem")),
                    "Inventario Feito": str(r.get("Inventario Feito", "Não"))
                })

            return jsonify({"sucesso": True, "itens": itens_formatados})
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({"sucesso": True, "itens": []})

    except Exception as e:
        print(f"Erro ao consultar movimentação: {e}")
        return jsonify({"sucesso": False, "mensagem": str(e)})






# --- ROTA PARA EXCLUIR MOVIMENTAÇÃO ---
@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()
    try:
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        sheet_mov = planilha.worksheet(data_mov)
        linhas = sheet_mov.get_all_values()
        for idx in range(1, len(linhas)):
            if str(linhas[idx][0]).strip() == row_id:
                sheet_mov.delete_rows(idx + 1)
                return jsonify({'sucesso': True, 'mensagem': 'Item excluído!'})
        return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

# --- ROTA PARA ATUALIZAR STATUS DE INVENTÁRIO FEITO ---
@app.route('/atualizar_status_lote_por_id', methods=['POST'])
def atualizar_status_lote_por_id():
    try:
        dados = request.get_json()
        data_consulta = str(dados.get('data', '')).strip()
        ids = [str(i).strip() for i in dados.get('ids', [])]
        novo_status = str(dados.get('status', 'Não')).strip()
        
        gc = conectar_google_sheets()
        aba = gc.open(NOME_PLANILHA).worksheet(data_consulta)
        linhas = aba.get_all_values()
        
        updates = []
        for idx, linha in enumerate(linhas[1:], start=2):
            if len(linha) > 0 and str(linha[0]).strip() in ids:
                # Atualiza a coluna H (Inventario Feito) em vez da F
                updates.append({'range': f'H{idx}', 'values': [[novo_status]]})
        if updates:
            aba.batch_update(updates)
        return jsonify({'sucesso': True, 'mensagem': 'Inventário Feito atualizado com sucesso!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})



# --- ROTA DE TESTE DIRETO ---
@app.route('/teste_direto_planilha', methods=['GET'])
def teste_direto_planilha():
    try:
        produtos = obter_produtos_cached()
        return jsonify({
            "sucesso": True,
            "total_linhas_cache": len(produtos),
            "exemplo": produtos[0] if produtos else {}
        })
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

if __name__ == '__main__':
    app.run(debug=True)





# --- ROTA PARA SALVAR O SALDO DE ESTOQUE ---
@app.route('/salvar_saldo', methods=['POST'])
def salvar_saldo():
    try:
        dados = request.json
        data_mes_ano = dados.get('data')  # Ex: "10/2026"
        texto_colado = dados.get('texto', '')

        if not data_mes_ano or not texto_colado:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

        # Nome da aba formatado conforme solicitado (ex: SALDO_10_2026)
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        # Processar as linhas coladas (separando código e saldo por TAB ou espaço)
        linhas_processadas = []
        for linha in texto_colado.strip().split('\n'):
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue
            
            partes = re.split(r'[\t\s]+', linha_limpa)
            if len(partes) >= 2:
                codigo = partes[0]
                saldo = partes[1]
                linhas_processadas.append([codigo, saldo, agora])
            elif len(partes) == 1:
                linhas_processadas.append([partes[0], '0', agora])

        if not linhas_processadas:
            return jsonify({"sucesso": False, "mensagem": "Nenhum dado válido encontrado no texto colado!"})

        # Conexão correta usando a função padrão do seu projeto
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)

        # O sistema verifica se a aba existe; se não existir, cria automaticamente
        try:
            aba = planilha.worksheet(nome_aba)
            aba.clear()  # Limpa os dados antigos para atualizar com o novo saldo colado
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=nome_aba, rows="1000", cols="3")

        # Escreve o cabeçalho e todas as linhas processadas de uma vez
        matriz_final = [["CODIGO", "SALDO", "ULTIMA ATUALIZACAO"]] + linhas_processadas
        aba.update('A1', matriz_final)

        return jsonify({
            "sucesso": True, 
            "mensagem": f"Saldo de {len(linhas_processadas)} item(ns) gravado na aba {nome_aba} com sucesso!"
        })

    except Exception as e:
        print(f"Erro ao salvar saldo: {e}")
        return jsonify({"sucesso": False, "mensagem": f"Erro interno: {str(e)}"}), 500


# --- ROTA PARA OBTER O SALDO E A DATA/HORA DA ÚLTIMA ATUALIZAÇÃO ---
@app.route('/obter_saldo', methods=['GET'])
def obter_saldo():
    try:
        data_mes_ano = request.args.get('data')
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()
            
            if len(registros) > 1:
                linhas_texto = []
                ultima_atualizacao = registros[1][2] if len(registros[1]) >= 3 else ''
                
                for r in registros[1:]:  # Ignora o cabeçalho
                    if len(r) >= 2:
                        linhas_texto.append(f"{r[0]}\t{r[1]}")
                
                return jsonify({
                    "sucesso": True,
                    "texto": "\n".join(linhas_texto),
                    "ultima_atualizacao": ultima_atualizacao
                })
        except Exception:
            pass

        return jsonify({
            "sucesso": True,
            "texto": "",
            "ultima_atualizacao": "Nenhuma atualização salva"
        })

    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": str(e)}), 500


# --- ROTA PARA CONSULTAR SALDO DE UM ITEM ESPECÍFICO ---
@app.route('/obter_saldo_item', methods=['GET'])
def obter_saldo_item():
    try:
        codigo_busca = str(request.args.get('codigo', '')).strip().upper()
        data_mes_ano = request.args.get('data', '') # Ex: "10/2026"
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        if not codigo_busca or not data_mes_ano:
            return jsonify({'sucesso': False, 'saldo': 0})

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()
            
            # Procura pelo código na coluna CODIGO (primeira coluna)
            for linha in registros[1:]:  # Pula cabeçalho
                if len(linha) >= 2:
                    cod_na_planilha = str(linha[0]).strip().upper()
                    if cod_na_planilha == codigo_busca:
                        return jsonify({
                            'sucesso': True,
                            'saldo': linha[1]
                        })
        except Exception:
            pass

        return jsonify({'sucesso': True, 'saldo': 0})

    except Exception as e:
        print(f"Erro ao obter saldo do item: {e}")
        return jsonify({'sucesso': False, 'saldo': 0})



# --- ROTA PARA EDITAR QUANTIDADE E RECALCULAR DIFERENÇA NA CONSULTA ---
@app.route('/atualizar_quantidade_lote', methods=['POST'])
def atualizar_quantidade_lote():
    try:
        dados = request.get_json()
        data_consulta = str(dados.get('data', '')).strip()
        row_id = str(dados.get('id', '')).strip()
        nova_qtd = int(dados.get('quantidade', 0))

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        aba = planilha.worksheet(data_consulta)
        linhas = aba.get_all_values()

        # Procura a linha pelo ID (coluna A / índice 0)
        for idx, linha in enumerate(linhas[1:], start=2):
            if len(linha) > 0 and str(linha[0]).strip() == row_id:
                # Coluna D (índice 3) é o Saldo Estoque
                saldo_estoque = int(linha[3]) if linha[3].isdigit() else 0
                nova_diferenca = nova_qtd - saldo_estoque

                # Atualiza no Sheets: Coluna E (Quantidade Contada) e Coluna F (Diferença)
                aba.update(f'E{idx}', [[nova_qtd]])
                aba.update(f'F{idx}', [[nova_diferenca]])

                return jsonify({
                    'sucesso': True, 
                    'mensagem': 'Quantidade atualizada e diferença recalculada com sucesso!',
                    'nova_diferenca': nova_diferenca
                })

        return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado na planilha.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao atualizar: {str(e)}'})






@app.route('/produtos_semi_acabados_faltam', methods=['GET'])
def produtos_semi_acabados_faltam():
    try:
        mes = request.args.get('mes', '').strip()
        ano = request.args.get('ano', '').strip()
        
        if not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Mês e ano são obrigatórios.'}), 400
            
        nome_aba_mes = f"{int(mes):02d}/{ano}"
        nome_aba_saldo = f"SALDO_{mes}_{ano}"
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(NOME_PLANILHA)
        
        # 1. Carrega todos os produtos oficiais da aba "dados" usando o cache existente
        produtos_oficiais = obter_produtos_cached()
        if not produtos_oficiais:
            try:
                aba_dados = planilha.worksheet("dados")
                dados_brutos = aba_dados.get_all_values()
                if len(dados_brutos) > 1:
                    for linha in dados_brutos[1:]:
                        if len(linha) >= 1 and str(linha[0]).strip():
                            produtos_oficiais.append({
                                'codigo': str(linha[0]).strip(),
                                'descricao': str(linha[1]).strip() if len(linha) > 1 else ''
                            })
            except Exception:
                pass

        if not produtos_oficiais:
            return jsonify({'sucesso': False, 'mensagem': 'Aba "dados" vazia ou não encontrada.'}), 400

        # 2. Mapeia quais códigos já foram inventariados/contados na aba do mês
        codigos_contados = set()
        try:
            aba_mes = planilha.worksheet(nome_aba_mes)
            linhas_mes = aba_mes.get_all_values()
            if len(linhas_mes) > 1:
                cabecalhos_mes = [str(c).strip().upper() for c in linhas_mes[0]]
                idx_cod = -1
                for idx, cab in enumerate(cabecalhos_mes):
                    if cab in ['CÓDIGO', 'CODIGO']:
                        idx_cod = idx
                        break
                if idx_cod == -1:
                    idx_cod = 2 # Padrão Coluna C
                
                for linha in linhas_mes[1:]:
                    if len(linha) > idx_cod:
                        cod = str(linha[idx_cod]).strip().upper()
                        if cod:
                            codigos_contados.add(cod)
        except Exception:
            pass

        # 3. Carrega os saldos da aba SALDO_MM_YYYY correspondente
        saldos_dict = {}
        try:
            aba_saldo = planilha.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:
                if len(linha) >= 2:
                    cod = str(linha[0]).strip().upper()
                    saldos_dict[cod] = str(linha[1]).strip()
        except Exception:
            pass

        # 4. Filtra rigorosamente apenas os produtos oficiais que NÃO estão na lista de contados
        produtos_pendentes = []
        for p in produtos_oficiais:
            cod = str(p.get('codigo', '')).strip()
            cod_upper = cod.upper()
            desc = str(p.get('descricao', ''))
            
            if cod and cod_upper not in codigos_contados:
                saldo_mes = saldos_dict.get(cod_upper, '-')
                if not saldo_mes:
                    saldo_mes = '-'
                    
                produtos_pendentes.append({
                    "codigo": cod,
                    "descricao": desc,
                    "saldo": saldo_mes
                })
                
        return jsonify({'sucesso': True, 'itens': produtos_pendentes})
        
    except Exception as e:
        print(f"Erro em produtos_semi_acabados_faltam: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500
