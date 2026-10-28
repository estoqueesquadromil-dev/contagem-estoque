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

# Nome oficial da planilha principal de contagem
NOME_PLANILHA = 'todos_produtos_contagem'
# Nome da planilha onde ficam as abas de saldo (ex: SALDO_10_2026)
NOME_PLANILHA_SALDOS = 'contagem_semi_acabado'
NOME_PLANILHA_ACESSORIOS = 'Acessórios_Primários'


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
    
    if CACHE_PRODUTOS and (agora - ULTIMA_ATUALIZACAO_CACHE < TEMPO_CACHE_SEGUNDOS):
        return CACHE_PRODUTOS

    try:
        print("[CACHE] Carregando produtos do Google Sheets...")
        gc = conectar_google_sheets()
        if not gc:
            return CACHE_PRODUTOS  
            
        planilha = gc.open(NOME_PLANILHA)
        # ALTERADO AQUI PARA LER DA ABA 'todos_produtos'
        sheet = planilha.worksheet('todos_produtos')
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
    data_lote = dados.get('data')  # Ex: "10/2026"
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        produtos = obter_produtos_cached()
        codigos_validos = {p['codigo'].upper() for p in produtos}

        # 1. Valida se todos os códigos enviados existem na planilha oficial
        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip().upper()
            if not cod_item:
                return jsonify({'sucesso': False, 'mensagem': 'Existe um item com código em branco!'})
            if cod_item not in codigos_validos:
                return jsonify({
                    'sucesso': False, 
                    'mensagem': f'Bloqueado! O código "{cod_item}" não existe na aba "todos_produtos".'
                })

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        
        # 2. Verifica se a aba do mês já existe e quais códigos já foram contados nela para evitar duplicados
        codigos_ja_existentes_no_mes = set()
        try:
            sheet_mov = planilha.worksheet(data_lote)
            registros_existentes = sheet_mov.get_all_records()
            for reg in registros_existentes:
                c_existente = str(reg.get('Código', '')).strip().upper()
                if c_existente:
                    codigos_ja_existentes_no_mes.add(c_existente)
        except gspread.exceptions.WorksheetNotFound:
            # Se a aba ainda não existe, cria agora com o cabeçalho correto
            sheet_mov = planilha.add_worksheet(title=data_lote, rows="1000", cols="8")
            sheet_mov.append_row(["ID", "Data", "Código", "Saldo Estoque", "Quantidade Contada", "Diferença", "Tipo", "Inventario Feito"])

        # Valida duplicidade contra o que já está na planilha do mês
        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip().upper()
            if cod_item in codigos_ja_existentes_no_mes:
                return jsonify({
                    'sucesso': False,
                    'mensagem': f'O produto "{cod_item}" já foi contado/adicionado neste mês ({data_lote}). Não é permitido duplicar!'
                })
            codigos_ja_existentes_no_mes.add(cod_item)

        linhas_adicionar = []
        for item in itens:
            row_id = str(uuid.uuid4())[:8]
            tipo_produto = str(item.get('TIPO', 'Semiacabado')).strip() # Pega o tipo exato escolhido no select
            
            linhas_adicionar.append([
                row_id,
                data_lote,
                item.get('CODIGO', ''),
                int(item.get('SALDO', 0)),
                int(item.get('QUANTIDADE', 1)),
                int(item.get('DIFERENCA', 0)),
                tipo_produto, # Grava o tipo correto (ex: Semiacabado) na coluna G
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

        # 1. Carregar o dicionário de saldos (Coluna B = Código, Coluna C = Saldo)
        saldos_dict = {}
        try:
            planilha_saldos = gc.open(NOME_PLANILHA_SALDOS)
            aba_saldo = planilha_saldos.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:  # Pula o cabeçalho
                if len(linha) >= 3:
                    cod = str(linha[1]).strip().upper() # Índice 1 = Código (Coluna B)
                    sld_str = str(linha[2]).strip().replace(',', '.') # Índice 2 = Saldo (Coluna C)
                    try:
                        sld = int(float(sld_str))
                    except ValueError:
                        sld = 0
                    saldos_dict[cod] = sld
        except Exception as e:
            print(f"[AVISO] Não foi possível ler a aba de saldo {nome_aba_saldo}: {e}")

        # 2. Carregar os itens da movimentação da aba do mês na planilha principal
        try:
            planilha_principal = gc.open(NOME_PLANILHA)
            sheet_mov = planilha_principal.worksheet(data_consulta)
            registros = sheet_mov.get_all_records()
            
            itens_formatados = []
            for r in registros:
                codigo = str(r.get("Código", "")).strip()
                codigo_upper = codigo.upper()
                
                # Pega o saldo correto mapeado do dicionário
                saldo_estoque = saldos_dict.get(codigo_upper, 0)
                
                qtd = r.get("Quantidade Contada", r.get("Quantidade", 0))
                try:
                    qtd_int = int(float(str(qtd).strip().replace(',', '.')))
                except ValueError:
                    qtd_int = 0
                
                diferenca = qtd_int - saldo_estoque

                itens_formatados.append({
                    "ID": str(r.get("ID", "")),
                    "Data": str(r.get("Data", "")),
                    "Código": codigo,
                    "SaldoEstoque": saldo_estoque,
                    "Quantidade": qtd_int,
                    "Diferenca": diferenca,
                    "Tipo": str(r.get("Tipo", "Semiacabado")),
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
# --- ROTA PARA SALVAR O SALDO DE ESTOQUE ---
# --- ROTA PARA SALVAR O SALDO DE ESTOQUE COM ID ---
@app.route('/salvar_saldo', methods=['POST'])
def salvar_saldo():
    try:
        dados = request.json
        data_mes_ano = dados.get('data')  # Ex: "10/2026"
        texto_colado = dados.get('texto', '')

        if not data_mes_ano or not texto_colado:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_SALDOS)

        try:
            aba = planilha.worksheet(nome_aba)
            registros_existentes = aba.get_all_records()
            # Mapeia IDs existentes para não perdê-los se o item já existia
            mapa_ids = {str(r.get('CODIGO', '')).strip().upper(): str(r.get('ID', '')).strip() for r in registros_existentes if r.get('CODIGO')}
            aba.clear()
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=nome_aba, rows="1000", cols="4")
            mapa_ids = {}

        linhas_processadas = []
        for linha in texto_colado.strip().split('\n'):
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue
            
            partes = re.split(r'[\t\s]+', linha_limpa)
            if len(partes) >= 2:
                codigo = partes[0].strip().upper()
                saldo = partes[1].strip()
                # Mantém o ID anterior se já existia, senão gera um novo ID curto
                row_id = mapa_ids.get(codigo) or str(uuid.uuid4())[:8]
                linhas_processadas.append([row_id, codigo, saldo, agora])
            elif len(partes) == 1:
                codigo = partes[0].strip().upper()
                row_id = mapa_ids.get(codigo) or str(uuid.uuid4())[:8]
                linhas_processadas.append([row_id, codigo, '0', agora])

        if not linhas_processadas:
            return jsonify({"sucesso": False, "mensagem": "Nenhum dado válido encontrado no texto colado!"})

        matriz_final = [["ID", "CODIGO", "SALDO", "ULTIMA ATUALIZACAO"]] + linhas_processadas
        aba.update('A1', matriz_final)

        return jsonify({
            "sucesso": True, 
            "mensagem": f"Saldo de {len(linhas_processadas)} item(ns) gravado com ID na planilha {NOME_PLANILHA_SALDOS} (aba {nome_aba}) com sucesso!"
        })

    except Exception as e:
        print(f"Erro ao salvar saldo: {e}")
        return jsonify({"sucesso": False, "mensagem": f"Erro interno: {str(e)}"}), 500


# --- ROTA PARA OBTER O SALDO (INCLUINDO ID) ---
@app.route('/obter_saldo', methods=['GET'])
def obter_saldo():
    try:
        data_mes_ano = request.args.get('data')
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_SALDOS)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()
            
            if len(registros) > 1:
                linhas_texto = []
                ultima_atualizacao = registros[1][3] if len(registros[1]) >= 4 else ''
                
                for r in registros[1:]:
                    if len(r) >= 3:
                        # Formato enviado: ID \t CODIGO \t SALDO
                        linhas_texto.append(f"{r[0]}\t{r[1]}\t{r[2]}")
                
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


# --- ROTA PARA EXCLUIR UM SALDO SALVO ESPECÍFICO PELO ID ---
@app.route('/excluir_saldo_salvo', methods=['POST'])
def excluir_saldo_salvo():
    try:
        dados = request.get_json()
        data_mes_ano = dados.get('data')
        row_id = str(dados.get('id', '')).strip()

        if not data_mes_ano or not row_id:
            return jsonify({'sucesso': False, 'mensagem': 'Data ou ID não informados!'}), 400

        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_SALDOS)
        aba = planilha.worksheet(nome_aba)
        
        linhas = aba.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'}), 404

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            if str(linhas[idx][0]).strip() == row_id:
                linha_encontrada = idx + 1 # Linha no Sheets (1-indexada)
                break

        if linha_encontrada != -1:
            aba.delete_rows(linha_encontrada)
            return jsonify({'sucesso': True, 'mensagem': 'Registro excluído com sucesso pelo ID!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'ID não encontrado na planilha de saldos.'}), 404

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao excluir: {str(e)}'}), 500








# --- ROTA PARA CONSULTAR SALDO DE UM ITEM ESPECÍFICO ---
@app.route('/obter_saldo_item', methods=['GET'])
def obter_saldo_item():
    try:
        codigo_busca = str(request.args.get('codigo', '')).strip().upper()
        data_mes_ano = request.args.get('data', '')
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        if not codigo_busca or not data_mes_ano:
            return jsonify({'sucesso': False, 'saldo': 0})

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_SALDOS)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()
            
            for linha in registros[1:]:
                if len(linha) >= 3:
                    cod_na_planilha = str(linha[1]).strip().upper() # Índice 1 = Código (Coluna B)
                    if cod_na_planilha == codigo_busca:
                        return jsonify({
                            'sucesso': True,
                            'saldo': linha[2] # Índice 2 = Saldo (Coluna C)
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
            
        nome_aba_mes = f"{int(mes):02d}/{ano}"             # Exemplo: "10/2026"
        nome_aba_saldo = f"SALDO_{int(mes):02d}_{ano}"     # Exemplo: "SALDO_10_2026"
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha_semi = gc.open(NOME_PLANILHA_SALDOS) # 'contagem_semi_acabado'

        # 1. Carrega o dicionário de saldos da aba SALDO_MM_YYYY (Coluna B = Código, Coluna C = Saldo)
        saldos_dict = {}
        try:
            aba_saldo = planilha_semi.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:  # Pula o cabeçalho [ID, CODIGO, SALDO, ...]
                if len(linha) >= 3:
                    cod = str(linha[1]).strip().upper()  # Índice 1 = Coluna B (Código)
                    sld = str(linha[2]).strip()          # Índice 2 = Coluna C (Saldo)
                    saldos_dict[cod] = sld
        except Exception as e:
            print(f"[AVISO] Aba de saldo {nome_aba_saldo} não encontrada em {NOME_PLANILHA_SALDOS}: {e}")

        # 2. Pega os códigos salvos na aba do mês (ex: 10/2026) da planilha 'contagem_semi_acabado'
        codigos_salvos_semi = []
        try:
            aba_semi_mes = planilha_semi.worksheet(nome_aba_mes)
            registros_semi = aba_semi_mes.get_all_records()
            for reg in registros_semi:
                cod = str(reg.get('Código', '')).strip().upper()
                if cod:
                    codigos_salvos_semi.append(cod)
        except Exception as e:
            print(f"[AVISO] Aba {nome_aba_mes} não encontrada em {NOME_PLANILHA_SALDOS}: {e}")
            return jsonify({'sucesso': True, 'itens': []})

        if not codigos_salvos_semi:
            return jsonify({'sucesso': True, 'itens': []})

        # 3. Pega os códigos que JÁ FORAM CONTADOS na planilha principal ('todos_produtos_contagem')
        codigos_ja_contados = set()
        try:
            planilha_principal = gc.open(NOME_PLANILHA) # 'todos_produtos_contagem'
            aba_principal_mes = planilha_principal.worksheet(nome_aba_mes)
            registros_principal = aba_principal_mes.get_all_records()
            for reg in registros_principal:
                cod = str(reg.get('Código', '')).strip().upper()
                if cod:
                    codigos_ja_contados.add(cod)
        except Exception as e:
            print(f"[AVISO] Aba {nome_aba_mes} ainda não criada na planilha principal: {e}")

        # 4. Filtra: traz o saldo correto para os produtos que ainda NÃO foram contados
        produtos_pendentes = []
        for cod in codigos_salvos_semi:
            if cod not in codigos_ja_contados:
                saldo_item = saldos_dict.get(cod, "0")
                produtos_pendentes.append({
                    "codigo": cod,
                    "descricao": "Semiacabado Pendente",
                    "saldo": saldo_item
                })
                
        return jsonify({'sucesso': True, 'itens': produtos_pendentes})
        
    except Exception as e:
        print(f"Erro em produtos_semi_acabados_faltam: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500





# --- ROTA PARA SALVAR CÓDIGOS PARA CONTAGEM NA PLANILHA contagem_semi_acabado ---
@app.route('/salvar_codigos_contar_semi', methods=['POST'])
def salvar_codigos_contar_semi():
    dados = request.json
    data_mes_ano = dados.get('data')  # Ex: "10/2026"
    codigos = dados.get('codigos', [])

    if not data_mes_ano or not codigos:
        return jsonify({'sucesso': False, 'mensagem': 'Informe o mês/ano e pelo menos um código!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        # Abre ESPECIFICAMENTE a planilha contagem_semi_acabado
        planilha = gc.open(NOME_PLANILHA_SALDOS) # NOME_PLANILHA_SALDOS = 'contagem_semi_acabado'

        # Procura ou cria a aba com o nome do mês e ano (Ex: "10/2026")
        try:
            aba = planilha.worksheet(data_mes_ano)
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=data_mes_ano, rows="1000", cols="8")
            aba.append_row(["ID", "Data", "Código", "Saldo Estoque", "Quantidade Contada", "Diferença", "Tipo", "Inventario Feito"])

        # Mapeia códigos já existentes na aba para evitar duplicados
        registros_existentes = aba.get_all_records()
        codigos_existentes = set()
        for reg in registros_existentes:
            c = str(reg.get('Código', '')).strip().upper()
            if c:
                codigos_existentes.add(c)

        linhas_adicionar = []
        for cod in codigos:
            cod_limpo = str(cod).strip().upper()
            if not cod_limpo or cod_limpo in codigos_existentes:
                continue
            
            row_id = str(uuid.uuid4())[:8]
            # Adiciona linha padrão para contagem de semi-acabado
            linhas_adicionar.append([
                row_id,
                data_mes_ano,
                cod_limpo,
                0,             # Saldo Estoque padrão
                0,             # Quantidade Contada inicial
                0,             # Diferença
                'Semiacabado', # Tipo
                'Não'          # Inventario Feito
            ])
            codigos_existentes.add(cod_limpo)

        if linhas_adicionar:
            aba.append_rows(linhas_adicionar)
            return jsonify({
                'sucesso': True, 
                'mensagem': f'{len(linhas_adicionar)} código(s) salvo(s) com sucesso na aba {data_mes_ano} da planilha contagem_semi_acabado!'
            })
        else:
            return jsonify({
                'sucesso': True, 
                'mensagem': 'Nenhum código novo para adicionar (já existem ou vazios).'
            })

    except Exception as e:
        print(f"Erro ao salvar códigos em contagem_semi_acabado: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'}), 500






# --- ROTA PARA SALVAR CÓDIGOS DE ACESSÓRIOS NA ABA DO MÊS ---
@app.route('/salvar_codigos_contar_acessorios', methods=['POST'])
def salvar_codigos_contar_acessorios():
    dados = request.json
    data_mes_ano = dados.get('data')  # Ex: "10/2026"
    codigos = dados.get('codigos', [])

    if not data_mes_ano or not codigos:
        return jsonify({'sucesso': False, 'mensagem': 'Informe o mês/ano e pelo menos um código!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha = gc.open(NOME_PLANILHA_ACESSORIOS)

        try:
            aba = planilha.worksheet(data_mes_ano)
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=data_mes_ano, rows="1000", cols="8")
            aba.append_row(["ID", "Data", "Código", "Saldo Estoque", "Quantidade Contada", "Diferença", "Tipo", "Inventario Feito"])

        registros_existentes = aba.get_all_records()
        codigos_existentes = set()
        for reg in registros_existentes:
            c = str(reg.get('Código', '')).strip().upper()
            if c:
                codigos_existentes.add(c)

        linhas_adicionar = []
        for cod in codigos:
            cod_limpo = str(cod).strip().upper()
            if not cod_limpo or cod_limpo in codigos_existentes:
                continue
            
            row_id = str(uuid.uuid4())[:8]
            linhas_adicionar.append([
                row_id,
                data_mes_ano,
                cod_limpo,
                0,
                0,
                0,
                'Acessório Primário',
                'Não'
            ])
            codigos_existentes.add(cod_limpo)

        if linhas_adicionar:
            aba.append_rows(linhas_adicionar)
            return jsonify({
                'sucesso': True, 
                'mensagem': f'{len(linhas_adicionar)} código(s) de acessórios salvo(s) com sucesso na aba {data_mes_ano}!'
            })
        else:
            return jsonify({
                'sucesso': True, 
                'mensagem': 'Nenhum código novo para adicionar (já existem ou vazios).'
            })

    except Exception as e:
        print(f"Erro ao salvar códigos em Acessórios_Primários: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'}), 500


# --- ROTA PARA SALVAR SALDO DE ACESSÓRIOS ---
@app.route('/salvar_saldo_acessorios', methods=['POST'])
def salvar_saldo_acessorios():
    try:
        dados = request.json
        data_mes_ano = dados.get('data')  # Ex: "10/2026"
        texto_colado = dados.get('texto', '')

        if not data_mes_ano or not texto_colado:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_ACESSORIOS)

        try:
            aba = planilha.worksheet(nome_aba)
            registros_existentes = aba.get_all_records()
            mapa_ids = {str(r.get('CODIGO', '')).strip().upper(): str(r.get('ID', '')).strip() for r in registros_existentes if r.get('CODIGO')}
            aba.clear()
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=nome_aba, rows="1000", cols="4")
            mapa_ids = {}

        linhas_processadas = []
        for linha in texto_colado.strip().split('\n'):
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue
            
            partes = re.split(r'[\t\s]+', linha_limpa)
            if len(partes) >= 2:
                codigo = partes[0].strip().upper()
                saldo = partes[1].strip()
                row_id = mapa_ids.get(codigo) or str(uuid.uuid4())[:8]
                linhas_processadas.append([row_id, codigo, saldo, agora])
            elif len(partes) == 1:
                codigo = partes[0].strip().upper()
                row_id = mapa_ids.get(codigo) or str(uuid.uuid4())[:8]
                linhas_processadas.append([row_id, codigo, '0', agora])

        if not linhas_processadas:
            return jsonify({"sucesso": False, "mensagem": "Nenhum dado válido encontrado!"})

        matriz_final = [["ID", "CODIGO", "SALDO", "ULTIMA ATUALIZACAO"]] + linhas_processadas
        aba.update('A1', matriz_final)

        return jsonify({
            "sucesso": True, 
            "mensagem": f"Saldo de {len(linhas_processadas)} acessório(s) gravado com sucesso!"
        })

    except Exception as e:
        print(f"Erro ao salvar saldo de acessórios: {e}")
        return jsonify({"sucesso": False, "mensagem": f"Erro interno: {str(e)}"}), 500


# --- ROTA PARA OBTER SALDO DE ACESSÓRIOS ---
@app.route('/obter_saldo_acessorios', methods=['GET'])
def obter_saldo_acessorios():
    try:
        data_mes_ano = request.args.get('data')
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_ACESSORIOS)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()
            
            if len(registros) > 1:
                linhas_texto = []
                ultima_atualizacao = registros[1][3] if len(registros[1]) >= 4 else ''
                
                for r in registros[1:]:
                    if len(r) >= 3:
                        linhas_texto.append(f"{r[0]}\t{r[1]}\t{r[2]}")
                
                return jsonify({
                    "sucesso": True,
                    "texto": "\n".join(linhas_texto),
                    "ultima_atualizacao": ultima_atualizacao
                })
        except Exception:
            pass

        return jsonify({"sucesso": True, "texto": "", "ultima_atualizacao": "Nenhuma atualização salva"})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": str(e)}), 500


# --- ROTA PARA OBTER PRODUTOS DE ACESSÓRIOS QUE FALTAM CONTAR ---
@app.route('/produtos_acessorios_faltam', methods=['GET'])
def produtos_acessorios_faltam():
    try:
        mes = request.args.get('mes', '').strip()
        ano = request.args.get('ano', '').strip()
        
        if not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Mês e ano são obrigatórios.'}), 400
            
        nome_aba_mes = f"{int(mes):02d}/{ano}"
        nome_aba_saldo = f"SALDO_{int(mes):02d}_{ano}"
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha_acessorios = gc.open(NOME_PLANILHA_ACESSORIOS)

        # 1. Carrega saldos da aba SALDO_MM_YYYY
        saldos_dict = {}
        try:
            aba_saldo = planilha_acessorios.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:
                if len(linha) >= 3:
                    cod = str(linha[1]).strip().upper()
                    sld = str(linha[2]).strip()
                    saldos_dict[cod] = sld
        except Exception:
            pass

        # 2. Pega códigos salvos na aba do mês na planilha de acessórios
        codigos_salvos = []
        try:
            aba_mes = planilha_acessorios.worksheet(nome_aba_mes)
            registros_semi = aba_mes.get_all_records()
            for reg in registros_semi:
                cod = str(reg.get('Código', '')).strip().upper()
                if cod:
                    codigos_salvos.append(cod)
        except Exception:
            return jsonify({'sucesso': True, 'itens': []})

        if not codigos_salvos:
            return jsonify({'sucesso': True, 'itens': []})

        # 3. Verifica quais já foram contados na planilha principal (filtrando apenas por "Acessório Primário")
        codigos_ja_contados = set()
        try:
            planilha_principal = gc.open(NOME_PLANILHA)
            aba_principal_mes = planilha_principal.worksheet(nome_aba_mes)
            registros_principal = aba_principal_mes.get_all_records()
            for reg in registros_principal:
                tipo = str(reg.get('Tipo', '')).strip()
                if tipo == 'Acessório Primário':
                    cod = str(reg.get('Código', '')).strip().upper()
                    if cod:
                        codigos_ja_contados.add(cod)
        except Exception:
            pass

        # 4. Filtra os pendentes
        produtos_pendentes = []
        for cod in codigos_salvos:
            if cod not in codigos_ja_contados:
                saldo_item = saldos_dict.get(cod, "0")
                produtos_pendentes.append({
                    "codigo": cod,
                    "descricao": "Acessório Primário Pendente",
                    "saldo": saldo_item
                })
                
        return jsonify({'sucesso': True, 'itens': produtos_pendentes})
        
    except Exception as e:
        print(f"Erro em produtos_acessorios_faltam: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


# --- ROTA PARA EXCLUIR SALDO DE ACESSÓRIOS PELO ID ---
@app.route('/excluir_saldo_acessorios', methods=['POST'])
def excluir_saldo_acessorios():
    try:
        dados = request.get_json()
        data_mes_ano = dados.get('data')
        row_id = str(dados.get('id', '')).strip()

        if not data_mes_ano or not row_id:
            return jsonify({'sucesso': False, 'mensagem': 'Data ou ID não informados!'}), 400

        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA_ACESSORIOS)
        aba = planilha.worksheet(nome_aba)
        
        linhas = aba.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'}), 404

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            if str(linhas[idx][0]).strip() == row_id:
                linha_encontrada = idx + 1
                break

        if linha_encontrada != -1:
            aba.delete_rows(linha_encontrada)
            return jsonify({'sucesso': True, 'mensagem': 'Registro excluído com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'ID não encontrado.'}), 404

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'}), 500
