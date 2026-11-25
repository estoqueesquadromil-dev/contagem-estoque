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


PLANILHAS_TIPOS = {
    'acessório primário': NOME_PLANILHA_ACESSORIOS,
    'acessório almoxarifado': 'Acessório Almoxarifado',
    'folha de porta madeira': 'Folha de Porta Madeira',
    'perfil': 'Perfil',
    'produto acabado': 'Produto Acabado',
    'subproduto': 'Subproduto',
    'vidro temperado': 'Vidro Temperado',
    'vidros': 'Vidros',
    'expediçao': 'Expediçao',
    'expedição': 'Expediçao',
}


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
        data_consulta = request.args.get('data')  # Ex: "11/2026"
        if not data_consulta:
            return jsonify({"sucesso": False, "mensagem": "Data não informada."})

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({"sucesso": False, "mensagem": "Erro de conexão com o Google Sheets."})

        def converter_para_inteiro(valor):
            if valor is None or valor == "":
                return 0
            if isinstance(valor, (int, float)):
                return int(valor)
            texto = str(valor).strip()
            # Trata formato brasileiro (ex: "1.200" -> 1200; "10,5" -> 10)
            if '.' in texto and ',' in texto:
                texto = texto.replace('.', '').replace(',', '.')
            elif '.' in texto:
                partes = texto.split('.')
                if len(partes[-1]) == 3 and all(p.isdigit() for p in partes):
                    texto = texto.replace('.', '')
            elif ',' in texto:
                texto = texto.replace(',', '.')
            try:
                return int(float(texto))
            except ValueError:
                return 0

        try:
            planilha_principal = gc.open(NOME_PLANILHA)
            sheet_mov = planilha_principal.worksheet(data_consulta)
            registros = sheet_mov.get_all_records()

            itens_formatados = []
            for r in registros:
                codigo = str(r.get("Código", "")).strip()
                tipo_prod = str(r.get("Tipo", "Semiacabado")).strip()

                # Usa o Saldo Estoque já salvo na contagem (não sobrescreve ao mudar o saldo geral)
                saldo_salvo = r.get("Saldo Estoque", 0)
                saldo_estoque = converter_para_inteiro(saldo_salvo)

                qtd = r.get("Quantidade Contada", r.get("Quantidade", 0))
                qtd_int = converter_para_inteiro(qtd)

                diferenca = qtd_int - saldo_estoque

                itens_formatados.append({
                    "ID": str(r.get("ID", "")),
                    "Data": str(r.get("Data", "")),
                    "Código": codigo,
                    "SaldoEstoque": saldo_estoque,
                    "Quantidade": qtd_int,
                    "Diferenca": diferenca,
                    "Tipo": tipo_prod,
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
        tipo_param = str(request.args.get('tipo', '') or '').strip().lower()
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        nome_planilha_alvo = PLANILHAS_TIPOS.get(tipo_param, NOME_PLANILHA_SALDOS)

        gc = conectar_google_sheets()
        planilha = gc.open(nome_planilha_alvo)

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
# --- ROTA PARA CONSULTAR SALDO DE UM ITEM ESPECÍFICO DINAMICAMENTE POR TIPO ---
@app.route('/obter_saldo_item', methods=['GET'])
def obter_saldo_item():
    try:
        codigo_busca = str(request.args.get('codigo', '')).strip().upper()
        data_mes_ano = request.args.get('data', '')
        tipo_produto = str(request.args.get('tipo', 'Semiacabado')).strip()
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"

        if not codigo_busca or not data_mes_ano:
            return jsonify({'sucesso': False, 'saldo': 0})

        # Define qual planilha consultar com base no tipo do produto usando PLANILHAS_TIPOS
        tipo_norm = tipo_produto.lower()
        if tipo_norm in PLANILHAS_TIPOS:
            nome_planilha_alvo = PLANILHAS_TIPOS[tipo_norm]
        else:
            nome_planilha_alvo = NOME_PLANILHA_SALDOS

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'saldo': 0})

        planilha = gc.open(nome_planilha_alvo)

        try:
            aba = planilha.worksheet(nome_aba)
            registros = aba.get_all_values()

            for linha in registros[1:]:
                if len(linha) >= 3:
                    cod_na_planilha = str(linha[1]).strip().upper()  # Coluna B = Código
                    if cod_na_planilha == codigo_busca:
                        # Limpa o saldo removendo o ponto de milhar (ex: "2.600" -> 2600)
                        saldo_str = str(linha[2]).strip().replace('.', '').replace(',', '.')
                        try:
                            saldo_limpo = int(float(saldo_str))
                        except ValueError:
                            saldo_limpo = 0

                        return jsonify({
                            'sucesso': True,
                            'saldo': saldo_limpo
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





@app.route('/metricas_acessorios', methods=['GET'])
def metricas_acessorios():
    try:
        mes = request.args.get('mes', '').strip()
        ano = request.args.get('ano', '').strip()

        if not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Mês e ano são obrigatórios.'}), 400

        nome_aba_mes = f"{int(mes):02d}/{ano}"

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        def para_int(valor):
            if valor is None or valor == "":
                return 0
            if isinstance(valor, (int, float)):
                return int(valor)
            texto = str(valor).strip()
            if '.' in texto and ',' in texto:
                texto = texto.replace('.', '').replace(',', '.')
            elif '.' in texto:
                partes = texto.split('.')
                if len(partes[-1]) == 3 and all(p.lstrip('-').isdigit() for p in partes):
                    texto = texto.replace('.', '')
            elif ',' in texto:
                texto = texto.replace(',', '.')
            try:
                return int(float(texto))
            except ValueError:
                return 0

        # 1. Busca os itens já contados na planilha principal (todos_produtos_contagem)
        contados_map = {}  # codigo -> {qtd, dif}
        try:
            planilha_principal = gc.open(NOME_PLANILHA)
            aba_principal = planilha_principal.worksheet(nome_aba_mes)
            registros_principal = aba_principal.get_all_records()

            for reg in registros_principal:
                tipo = str(reg.get('Tipo', '')).strip()
                if tipo == 'Acessório Primário':
                    cod = str(reg.get('Código', '')).strip().upper()
                    if not cod:
                        continue
                    saldo = para_int(reg.get('Saldo Estoque', 0))
                    qtd = para_int(reg.get('Quantidade Contada', reg.get('Quantidade', 0)))
                    dif = qtd - saldo
                    contados_map[cod] = {'qtd': qtd, 'dif': dif}
        except Exception:
            pass

        # 2. Busca a lista planejada de acessórios na planilha Acessórios_Primários
        codigos_planejados = []
        try:
            planilha_acessorios = gc.open(NOME_PLANILHA_ACESSORIOS)
            aba_acessorios = planilha_acessorios.worksheet(nome_aba_mes)
            registros_acess = aba_acessorios.get_all_records()
            for reg in registros_acess:
                cod = str(reg.get('Código', '')).strip().upper()
                if cod and cod not in codigos_planejados:
                    codigos_planejados.append(cod)
        except Exception:
            pass

        # Cálculos do Bloco 1: Acurácia por SKU
        qtd_contados_sku = len(contados_map)
        qtd_corretos_sku = sum(1 for item in contados_map.values() if item['dif'] == 0)
        acuracia_sku = round((qtd_corretos_sku / qtd_contados_sku) * 100, 2) if qtd_contados_sku > 0 else 0.0

        # Cálculos do Bloco 2: Índice de Divergência Líquida
        volume_contado = sum(item['qtd'] for item in contados_map.values())
        soma_diferenca = sum(item['dif'] for item in contados_map.values())
        indice_divergencia = round((soma_diferenca / volume_contado) * 100, 2) if volume_contado > 0 else 0.0

        # Cálculos do Bloco 3: Eficiência de Contagem
        falta_contar = sum(1 for cod in codigos_planejados if cod not in contados_map)
        total_universo = qtd_contados_sku + falta_contar
        eficiencia = round((qtd_contados_sku / total_universo) * 100, 2) if total_universo > 0 else 0.0

        return jsonify({
            'sucesso': True,
            'acuracia': {
                'contados': qtd_contados_sku,
                'corretos': qtd_corretos_sku,
                'percentual': acuracia_sku
            },
            'divergencia': {
                'volume_contado': volume_contado,
                'soma_diferenca': soma_diferenca,
                'percentual': indice_divergencia
            },
            'eficiencia': {
                'contados': qtd_contados_sku,
                'falta_contar': falta_contar,
                'percentual': eficiencia
            }
        })

    except Exception as e:
        print(f"Erro em metricas_acessorios: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/metricas_semi_acabado', methods=['GET'])
def metricas_semi_acabado():
    try:
        mes = request.args.get('mes', '').strip()
        ano = request.args.get('ano', '').strip()

        if not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Mês e ano são obrigatórios.'}), 400

        nome_aba_mes = f"{int(mes):02d}/{ano}"

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        def para_int(valor):
            if valor is None or valor == "":
                return 0
            if isinstance(valor, (int, float)):
                return int(valor)
            texto = str(valor).strip()
            if '.' in texto and ',' in texto:
                texto = texto.replace('.', '').replace(',', '.')
            elif '.' in texto:
                partes = texto.split('.')
                if len(partes[-1]) == 3 and all(p.lstrip('-').isdigit() for p in partes):
                    texto = texto.replace('.', '')
            elif ',' in texto:
                texto = texto.replace(',', '.')
            try:
                return int(float(texto))
            except ValueError:
                return 0

        # 1. Puxa direto da planilha todos_produtos_contagem (aba MM/YYYY) onde Tipo == Semiacabado
        itens_semi = []
        codigos_contados_set = set()
        try:
            planilha_principal = gc.open(NOME_PLANILHA)
            aba_principal = planilha_principal.worksheet(nome_aba_mes)
            registros_principal = aba_principal.get_all_records()

            for reg in registros_principal:
                tipo_norm = str(reg.get('Tipo', '')).strip().lower()
                if tipo_norm in ('semiacabado', 'semi-acabado', 'semi acabado'):
                    cod = str(reg.get('Código', '')).strip().upper()
                    saldo = para_int(reg.get('Saldo Estoque', reg.get('Saldo', 0)))
                    qtd = para_int(reg.get('Quantidade Contada', reg.get('Quantidade', 0)))
                    dif = qtd - saldo
                    itens_semi.append({'codigo': cod, 'qtd': qtd, 'dif': dif})
                    if cod:
                        codigos_contados_set.add(cod)
        except Exception as e:
            print(f"[AVISO] Erro ao ler {nome_aba_mes} em {NOME_PLANILHA}: {e}")

        # 2. Busca pendências cadastradas na planilha contagem_semi_acabado (aba MM/YYYY)
        codigos_planejados = []
        try:
            planilha_semi = gc.open(NOME_PLANILHA_SALDOS)
            aba_semi = planilha_semi.worksheet(nome_aba_mes)
            registros_semi = aba_semi.get_all_records()
            for reg in registros_semi:
                cod = str(reg.get('Código', '')).strip().upper()
                if cod and cod not in codigos_planejados:
                    codigos_planejados.append(cod)
        except Exception:
            pass

        # Card 1: Acurácia por SKU
        qtd_contados_sku = len(itens_semi)
        qtd_corretos_sku = sum(1 for item in itens_semi if item['dif'] == 0)
        acuracia_sku = round((qtd_corretos_sku / qtd_contados_sku) * 100, 2) if qtd_contados_sku > 0 else 0.0

        # Card 2: Índice de Divergência Líquida
        volume_contado = sum(item['qtd'] for item in itens_semi)
        soma_diferenca = sum(item['dif'] for item in itens_semi)
        indice_divergencia = round((soma_diferenca / volume_contado) * 100, 2) if volume_contado > 0 else 0.0

        # Card 3: Eficiência de Contagem
        falta_contar = sum(1 for cod in codigos_planejados if cod not in codigos_contados_set)
        total_universo = qtd_contados_sku + falta_contar
        eficiencia = round((qtd_contados_sku / total_universo) * 100, 2) if total_universo > 0 else 0.0

        return jsonify({
            'sucesso': True,
            'acuracia': {
                'contados': qtd_contados_sku,
                'corretos': qtd_corretos_sku,
                'percentual': acuracia_sku
            },
            'divergencia': {
                'volume_contado': volume_contado,
                'soma_diferenca': soma_diferenca,
                'percentual': indice_divergencia
            },
            'eficiencia': {
                'contados': qtd_contados_sku,
                'falta_contar': falta_contar,
                'percentual': eficiencia
            }
        })

    except Exception as e:
        print(f"Erro em metricas_semi_acabado: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500






def _converter_float_br(valor):
    if valor is None or valor == "":
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace('%', '')
    if '.' in texto and ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif '.' in texto:
        partes = texto.split('.')
        if len(partes[-1]) == 3 and all(p.lstrip('-').isdigit() for p in partes):
            texto = texto.replace('.', '')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        return float(texto)
    except ValueError:
        return 0.0

def _abrir_aba_mes_flexivel(gc, nome_planilha, mes, ano):
    mes_int = int(mes)
    ano_int = int(ano)
    candidatos = [
        f"{mes_int:02d}/{ano_int}",
        f"SALDO_{mes_int:02d}_{ano_int}",
    ]
    planilha = gc.open(nome_planilha)
    for nome_aba in candidatos:
        try:
            return planilha.worksheet(nome_aba)
        except gspread.exceptions.WorksheetNotFound:
            continue
    raise gspread.exceptions.WorksheetNotFound(f"Nenhuma aba encontrada ({candidatos}) em {nome_planilha}")




def _calcular_percentuais_tipo(gc, mes, ano, tipo_alvo, falta_contar_manual=None):
    nome_aba_mes = f"{int(mes):02d}/{ano}"
    tipo_norm = str(tipo_alvo or '').strip().lower()
    eh_semi = tipo_norm in ('semiacabado', 'semi-acabado', 'semi acabado')

    itens = []
    codigos_contados = set()

    try:
        aba_principal = _abrir_aba_mes_flexivel(gc, NOME_PLANILHA, mes, ano)
        registros = aba_principal.get_all_records()

        for reg in registros:
            reg_tipo_norm = str(reg.get('Tipo', '')).strip().lower()
            reg_eh_semi = reg_tipo_norm in ('semiacabado', 'semi-acabado', 'semi acabado')
            if (eh_semi and reg_eh_semi) or (not eh_semi and reg_tipo_norm == tipo_norm):
                cod = str(reg.get('Código', '')).strip().upper()
                saldo = _converter_float_br(reg.get('Saldo Estoque', reg.get('Saldo', 0)))
                qtd = _converter_float_br(reg.get('Quantidade Contada', reg.get('Quantidade', 0)))
                dif = qtd - saldo
                itens.append({'codigo': cod, 'qtd': qtd, 'dif': dif})
                if cod:
                    codigos_contados.add(cod)
    except Exception as e:
        print(f"[AVISO] Falha ao ler {nome_aba_mes} em {NOME_PLANILHA} ({tipo_alvo}): {e}")

    qtd_contados_sku = len(itens)
    qtd_corretos_sku = sum(1 for item in itens if item['dif'] == 0)
    acuracia_sku = round((qtd_corretos_sku / qtd_contados_sku) * 100, 2) if qtd_contados_sku > 0 else 0.0

    volume_contado = sum(item['qtd'] for item in itens)
    soma_diferenca = sum(item['dif'] for item in itens)
    indice_divergencia = round((soma_diferenca / volume_contado) * 100, 2) if volume_contado > 0 else 0.0

    # Quantidade manual só é permitida para Semiacabado; os demais tipos calculam automaticamente
    if eh_semi and falta_contar_manual is not None and str(falta_contar_manual).strip() != "":
        falta_contar = max(0.0, _converter_float_br(falta_contar_manual))
    else:
        codigos_planejados = []
        try:
            if tipo_norm in PLANILHAS_TIPOS:
                aba_ref = _abrir_aba_mes_flexivel(gc, PLANILHAS_TIPOS[tipo_norm], mes, ano)
                for reg in aba_ref.get_all_records():
                    cod = str(reg.get('Código', '')).strip().upper()
                    if cod and cod not in codigos_planejados:
                        codigos_planejados.append(cod)
            elif eh_semi:
                aba_ref = _abrir_aba_mes_flexivel(gc, NOME_PLANILHA_SALDOS, mes, ano)
                for reg in aba_ref.get_all_records():
                    cod = str(reg.get('Código', '')).strip().upper()
                    if cod and cod not in codigos_planejados:
                        codigos_planejados.append(cod)
        except Exception:
            pass
        falta_contar = float(sum(1 for cod in codigos_planejados if cod not in codigos_contados))

    total_universo = qtd_contados_sku + falta_contar
    eficiencia = round((qtd_contados_sku / total_universo) * 100, 2) if total_universo > 0 else 0.0

    return {
        'Acuracia_SKU': acuracia_sku,
        'Indice_Divergencia_Liquida': indice_divergencia,
        'Eficiencia_Contagem': eficiencia
    }

def _garantir_aba_eficiencia(gc):
    planilha_ef = gc.open('eficiencia')
    try:
        aba_ef = planilha_ef.worksheet('eficiencia')
    except gspread.exceptions.WorksheetNotFound:
        aba_ef = planilha_ef.sheet1

    cabecalho = [
        "Ano",
        "Mes",
        "Tipo",
        "Acuracia_SKU",
        "Indice_Divergencia_Liquida",
        "Eficiencia_Contagem"
    ]

    linhas = aba_ef.get_all_values()
    if not linhas:
        aba_ef.append_row(cabecalho, value_input_option='USER_ENTERED')
        linhas = [cabecalho]
    return aba_ef, linhas


def _gravar_linha_eficiencia(gc, ano_int, mes_int, tipo_limpo, acuracia_val, div_val, efic_val):
    aba_ef, linhas = _garantir_aba_eficiencia(gc)

    nova_linha = [
        int(ano_int),
        int(mes_int),
        str(tipo_limpo).strip(),
        round(float(acuracia_val), 2),
        round(float(div_val), 2),
        round(float(efic_val), 2)
    ]

    linha_existente = None
    for idx in range(1, len(linhas)):
        r = linhas[idx]
        if len(r) >= 3:
            r_ano = str(r[0]).strip()
            r_mes = str(r[1]).strip().lstrip('0') or '0'
            r_tipo = str(r[2]).strip().lower()
            if r_ano == str(int(ano_int)) and r_mes == str(int(mes_int)) and r_tipo == str(tipo_limpo).strip().lower():
                linha_existente = idx + 1
                break

    if linha_existente is not None:
        aba_ef.update(
            range_name=f'A{linha_existente}:F{linha_existente}',
            values=[nova_linha],
            value_input_option='USER_ENTERED'
        )
        return 'atualizada', nova_linha
    else:
        aba_ef.append_row(nova_linha, value_input_option='USER_ENTERED')
        return 'adicionada', nova_linha


def _gravar_lote_eficiencia(gc, ano_int, mes_int, itens_percentuais):
    """Grava/atualiza vários tipos em uma única operação de lote no Google Sheets."""
    aba_ef, linhas = _garantir_aba_eficiencia(gc)

    mapa_linhas = {}
    for idx in range(1, len(linhas)):
        r = linhas[idx]
        if len(r) >= 3:
            r_ano = str(r[0]).strip()
            r_mes = str(r[1]).strip().lstrip('0') or '0'
            r_tipo = str(r[2]).strip().lower()
            if r_ano == str(int(ano_int)) and r_mes == str(int(mes_int)):
                mapa_linhas[r_tipo] = idx + 1

    batch_updates = []
    novas_linhas = []
    salvos = []

    for item in itens_percentuais:
        tipo_limpo = str(item['Tipo']).strip()
        nova_linha = [
            int(ano_int),
            int(mes_int),
            tipo_limpo,
            round(float(item['Acuracia_SKU']), 2),
            round(float(item['Indice_Divergencia_Liquida']), 2),
            round(float(item['Eficiencia_Contagem']), 2)
        ]

        chave_tipo = tipo_limpo.lower()
        if chave_tipo in mapa_linhas:
            num_linha = mapa_linhas[chave_tipo]
            batch_updates.append({
                'range': f'A{num_linha}:F{num_linha}',
                'values': [nova_linha]
            })
            acao = 'atualizada'
        else:
            novas_linhas.append(nova_linha)
            acao = 'adicionada'

        salvos.append({
            'Tipo': tipo_limpo,
            'acao': acao,
            'Acuracia_SKU': nova_linha[3],
            'Indice_Divergencia_Liquida': nova_linha[4],
            'Eficiencia_Contagem': nova_linha[5]
        })

    if batch_updates:
        aba_ef.batch_update(batch_updates, value_input_option='USER_ENTERED')
    if novas_linhas:
        aba_ef.append_rows(novas_linhas, value_input_option='USER_ENTERED')

    return salvos


@app.route('/sincronizar_eficiencia_todos_tipos', methods=['POST'])
def sincronizar_eficiencia_todos_tipos():
    try:
        dados = request.get_json() or {}
        mes = str(dados.get('mes', '')).strip()
        ano = str(dados.get('ano', '')).strip()
        falta_semi_manual = dados.get('falta_contar_semi', None)

        if not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Informe Mês e Ano.'}), 400

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        tipos_padrao = [
            "Produto Acabado",
            "Acessório Primário",
            "Acessório Almoxarifado",
            "Folha de Porta Madeira",
            "Perfil",
            "Vidro Temperado",
            "Vidros",
            "Semiacabado",
            "Subproduto",
            "Expediçao",
        ]

        registros_mes = []
        try:
            aba_principal = _abrir_aba_mes_flexivel(gc, NOME_PLANILHA, mes, ano)
            registros_mes = aba_principal.get_all_records()
        except Exception:
            registros_mes = []

        # Agrupa os registros do mês em memória para evitar abrir a planilha várias vezes
        grupos_por_tipo = {}
        tipos_encontrados = set(tipos_padrao)

        for reg in registros_mes:
            t_raw = str(reg.get('Tipo', '')).strip()
            if not t_raw:
                continue
            eh_semi = t_raw.lower() in ('semiacabado', 'semi-acabado', 'semi acabado')
            tipo_chave = 'Semiacabado' if eh_semi else t_raw
            tipos_encontrados.add(tipo_chave)

            cod = str(reg.get('Código', '')).strip().upper()
            saldo = _converter_float_br(reg.get('Saldo Estoque', reg.get('Saldo', 0)))
            qtd = _converter_float_br(reg.get('Quantidade Contada', reg.get('Quantidade', 0)))
            dif = qtd - saldo

            grupos_por_tipo.setdefault(tipo_chave.lower(), []).append({
                'codigo': cod,
                'qtd': qtd,
                'dif': dif
            })

        # Mapeia códigos já contados por tipo
        codigos_contados_por_tipo = {
            t_chave: {it['codigo'] for it in itens_l if it.get('codigo')}
            for t_chave, itens_l in grupos_por_tipo.items()
        }

        # Lê códigos planejados por tipo (uma leitura por planilha de referência)
        planejados_por_tipo = {}

        def _carregar_codigos_planilha(nome_planilha_ref, chave_destino):
            try:
                aba_ref = _abrir_aba_mes_flexivel(gc, nome_planilha_ref, mes, ano)
                registros_ref = aba_ref.get_all_records()
                tem_coluna_tipo = False

                for reg in registros_ref:
                    cod = str(reg.get('Código', '')).strip().upper()
                    if not cod:
                        continue
                    t_reg = str(reg.get('Tipo', '')).strip()
                    if t_reg:
                        tem_coluna_tipo = True
                        eh_semi_reg = t_reg.lower() in ('semiacabado', 'semi-acabado', 'semi acabado')
                        chave_t = 'semiacabado' if eh_semi_reg else t_reg.lower()
                        lista_t = planejados_por_tipo.setdefault(chave_t, [])
                        if cod not in lista_t:
                            lista_t.append(cod)

                # Se a planilha de referência não tiver coluna "Tipo", associa ao tipo de destino
                if not tem_coluna_tipo and chave_destino:
                    lista_fb = planejados_por_tipo.setdefault(chave_destino, [])
                    for reg in registros_ref:
                        cod = str(reg.get('Código', '')).strip().upper()
                        if cod and cod not in lista_fb:
                            lista_fb.append(cod)
            except Exception:
                pass

        _carregar_codigos_planilha(NOME_PLANILHA_SALDOS, 'semiacabado')

        planilhas_unicas = {}
        for chave_t, nome_p in PLANILHAS_TIPOS.items():
            if chave_t == 'expedição':
                continue
            planilhas_unicas[chave_t] = nome_p

        for chave_t, nome_p in planilhas_unicas.items():
            _carregar_codigos_planilha(nome_p, chave_t)

        itens_calculados = []
        for tipo_item in sorted(tipos_encontrados):
            tipo_lower = tipo_item.lower()
            eh_semi = tipo_lower in ('semiacabado', 'semi-acabado', 'semi acabado')
            chave_tipo = 'semiacabado' if eh_semi else tipo_lower

            itens_tipo = grupos_por_tipo.get(chave_tipo, [])
            qtd_contados_sku = len(itens_tipo)
            qtd_corretos_sku = sum(1 for it in itens_tipo if it['dif'] == 0)
            acuracia_sku = round((qtd_corretos_sku / qtd_contados_sku) * 100, 2) if qtd_contados_sku > 0 else 0.0

            volume_contado = sum(it['qtd'] for it in itens_tipo)
            soma_diferenca = sum(it['dif'] for it in itens_tipo)
            indice_divergencia = round((soma_diferenca / volume_contado) * 100, 2) if volume_contado > 0 else 0.0

            contados_set = codigos_contados_por_tipo.get(chave_tipo, set())

            if eh_semi and falta_semi_manual is not None and str(falta_semi_manual).strip() != "":
                falta_contar = max(0.0, _converter_float_br(falta_semi_manual))
            else:
                planejados_tipo = planejados_por_tipo.get(chave_tipo, [])
                falta_contar = float(sum(1 for cod in planejados_tipo if cod not in contados_set))

            total_universo = qtd_contados_sku + falta_contar
            eficiencia = round((qtd_contados_sku / total_universo) * 100, 2) if total_universo > 0 else 0.0

            itens_calculados.append({
                'Tipo': tipo_item,
                'Acuracia_SKU': acuracia_sku,
                'Indice_Divergencia_Liquida': indice_divergencia,
                'Eficiencia_Contagem': eficiencia
            })

        salvos = _gravar_lote_eficiencia(gc, int(ano), int(mes), itens_calculados)

        return jsonify({
            'sucesso': True,
            'mensagem': f'{len(salvos)} tipo(s) enviados em lote para a planilha "eficiencia" ({int(mes):02d}/{int(ano)}).',
            'registros': salvos
        })
    except Exception as e:
        print(f"Erro em sincronizar_eficiencia_todos_tipos: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao sincronizar tipos: {str(e)}'}), 500


@app.route('/salvar_eficiencia', methods=['POST'])
def salvar_eficiencia():
    try:
        dados = request.get_json() or {}
        mes = str(dados.get('mes', '')).strip()
        ano = str(dados.get('ano', '')).strip()
        tipo = str(dados.get('tipo', '')).strip()
        falta_manual = dados.get('falta_contar', None)

        if not mes or not ano or not tipo:
            return jsonify({'sucesso': False, 'mensagem': 'Informe Ano, Mês e Tipo.'}), 400

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        perc = _calcular_percentuais_tipo(gc, mes, ano, tipo, falta_manual)

        acuracia_val = _converter_float_br(dados.get('acuracia_sku', perc['Acuracia_SKU']))
        div_val = _converter_float_br(dados.get('indice_divergencia_liquida', perc['Indice_Divergencia_Liquida']))
        efic_val = _converter_float_br(dados.get('eficiencia_contagem', perc['Eficiencia_Contagem']))

        acao, linha_gravada = _gravar_linha_eficiencia(
            gc, int(ano), int(mes), tipo, acuracia_val, div_val, efic_val
        )

        return jsonify({
            'sucesso': True,
            'mensagem': f'Percentuais ({tipo} {int(mes):02d}/{int(ano)}) {acao} na planilha "eficiencia"!',
            'acao': acao,
            'dados': {
                'Ano': linha_gravada[0],
                'Mes': linha_gravada[1],
                'Tipo': linha_gravada[2],
                'Acuracia_SKU': linha_gravada[3],
                'Indice_Divergencia_Liquida': linha_gravada[4],
                'Eficiencia_Contagem': linha_gravada[5]
            }
        })
    except Exception as e:
        print(f"Erro em salvar_eficiencia: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao enviar para a planilha eficiencia: {str(e)}'}), 500



@app.route('/produtos_tipo_faltam', methods=['GET'])
def produtos_tipo_faltam():
    try:
        tipo_raw = str(request.args.get('tipo', '')).strip()
        mes = str(request.args.get('mes', '')).strip()
        ano = str(request.args.get('ano', '')).strip()

        if not tipo_raw or not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Tipo, mês e ano são obrigatórios.'}), 400

        tipo_norm = tipo_raw.lower()
        if tipo_norm not in PLANILHAS_TIPOS:
            return jsonify({'sucesso': False, 'mensagem': f'Tipo "{tipo_raw}" não mapeado em PLANILHAS_TIPOS.'}), 400

        nome_planilha_tipo = PLANILHAS_TIPOS[tipo_norm]
        nome_aba_mes = f"{int(mes):02d}/{ano}"
        nome_aba_saldo = f"SALDO_{int(mes):02d}_{ano}"

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_tipo = gc.open(nome_planilha_tipo)

        saldos_dict = {}
        try:
            aba_saldo = planilha_tipo.worksheet(nome_aba_saldo)
            registros_saldo = aba_saldo.get_all_values()
            for linha in registros_saldo[1:]:
                if len(linha) >= 3:
                    cod = str(linha[1]).strip().upper()
                    sld = str(linha[2]).strip()
                    if cod:
                        saldos_dict[cod] = sld
        except Exception:
            pass

        codigos_salvos = []
        try:
            aba_mes = _abrir_aba_mes_flexivel(gc, nome_planilha_tipo, mes, ano)
            for reg in aba_mes.get_all_records():
                cod = str(reg.get('Código', '')).strip().upper()
                if cod and cod not in codigos_salvos:
                    codigos_salvos.append(cod)
        except Exception:
            return jsonify({'sucesso': True, 'itens': []})

        if not codigos_salvos:
            return jsonify({'sucesso': True, 'itens': []})

        codigos_ja_contados = set()
        try:
            aba_principal = _abrir_aba_mes_flexivel(gc, NOME_PLANILHA, mes, ano)
            for reg in aba_principal.get_all_records():
                reg_tipo = str(reg.get('Tipo', '')).strip().lower()
                if reg_tipo == tipo_norm or (tipo_norm in ('expediçao', 'expedição') and reg_tipo in ('expediçao', 'expedição')):
                    cod = str(reg.get('Código', '')).strip().upper()
                    if cod:
                        codigos_ja_contados.add(cod)
        except Exception:
            pass

        produtos_pendentes = []
        for cod in codigos_salvos:
            if cod not in codigos_ja_contados:
                produtos_pendentes.append({
                    "codigo": cod,
                    "descricao": f"{tipo_raw} Pendente",
                    "saldo": saldos_dict.get(cod, "0")
                })

        return jsonify({'sucesso': True, 'itens': produtos_pendentes})
    except Exception as e:
        print(f"Erro em produtos_tipo_faltam: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/metricas_tipo', methods=['GET'])
def metricas_tipo():
    try:
        tipo_raw = str(request.args.get('tipo', '')).strip()
        mes = str(request.args.get('mes', '')).strip()
        ano = str(request.args.get('ano', '')).strip()

        if not tipo_raw or not mes or not ano:
            return jsonify({'sucesso': False, 'mensagem': 'Tipo, mês e ano são obrigatórios.'}), 400

        tipo_norm = tipo_raw.lower()
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        contados_map = {}
        try:
            aba_principal = _abrir_aba_mes_flexivel(gc, NOME_PLANILHA, mes, ano)
            for reg in aba_principal.get_all_records():
                reg_tipo = str(reg.get('Tipo', '')).strip().lower()
                if reg_tipo == tipo_norm or (tipo_norm in ('expediçao', 'expedição') and reg_tipo in ('expediçao', 'expedição')):
                    cod = str(reg.get('Código', '')).strip().upper()
                    if not cod:
                        continue
                    saldo = int(_converter_float_br(reg.get('Saldo Estoque', reg.get('Saldo', 0))))
                    qtd = int(_converter_float_br(reg.get('Quantidade Contada', reg.get('Quantidade', 0))))
                    dif = qtd - saldo
                    contados_map[cod] = {'qtd': qtd, 'dif': dif}
        except Exception:
            pass

        codigos_planejados = []
        if tipo_norm in PLANILHAS_TIPOS:
            try:
                aba_ref = _abrir_aba_mes_flexivel(gc, PLANILHAS_TIPOS[tipo_norm], mes, ano)
                for reg in aba_ref.get_all_records():
                    cod = str(reg.get('Código', '')).strip().upper()
                    if cod and cod not in codigos_planejados:
                        codigos_planejados.append(cod)
            except Exception:
                pass

        qtd_contados_sku = len(contados_map)
        qtd_corretos_sku = sum(1 for item in contados_map.values() if item['dif'] == 0)
        acuracia_sku = round((qtd_corretos_sku / qtd_contados_sku) * 100, 2) if qtd_contados_sku > 0 else 0.0

        volume_contado = sum(item['qtd'] for item in contados_map.values())
        soma_diferenca = sum(item['dif'] for item in contados_map.values())
        indice_divergencia = round((soma_diferenca / volume_contado) * 100, 2) if volume_contado > 0 else 0.0

        falta_contar = sum(1 for cod in codigos_planejados if cod not in contados_map)
        total_universo = qtd_contados_sku + falta_contar
        eficiencia = round((qtd_contados_sku / total_universo) * 100, 2) if total_universo > 0 else 0.0

        return jsonify({
            'sucesso': True,
            'acuracia': {
                'contados': qtd_contados_sku,
                'corretos': qtd_corretos_sku,
                'percentual': acuracia_sku
            },
            'divergencia': {
                'volume_contado': volume_contado,
                'soma_diferenca': soma_diferenca,
                'percentual': indice_divergencia
            },
            'eficiencia': {
                'contados': qtd_contados_sku,
                'falta_contar': falta_contar,
                'percentual': eficiencia
            }
        })
    except Exception as e:
        print(f"Erro em metricas_tipo: {e}")
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500



@app.route('/salvar_saldo_tipo', methods=['POST'])
def salvar_saldo_tipo():
    try:
        dados = request.get_json() or {}
        tipo_raw = str(dados.get('tipo', '')).strip()
        data_mes_ano = str(dados.get('data', '')).strip()
        texto_colado = str(dados.get('texto', '')).strip()

        tipo_norm = tipo_raw.lower()
        if not tipo_raw or tipo_norm not in PLANILHAS_TIPOS or not data_mes_ano or not texto_colado:
            return jsonify({'sucesso': False, 'mensagem': 'Tipo, data ou texto inválidos!'})

        nome_planilha_alvo = PLANILHAS_TIPOS[tipo_norm]
        nome_aba = f"SALDO_{data_mes_ano.replace('/', '_')}"
        agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        gc = conectar_google_sheets()
        planilha = gc.open(nome_planilha_alvo)

        try:
            aba = planilha.worksheet(nome_aba)
            registros_existentes = aba.get_all_records()
            mapa_ids = {
                str(r.get('CODIGO', '')).strip().upper(): str(r.get('ID', '')).strip()
                for r in registros_existentes if r.get('CODIGO')
            }
            aba.clear()
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=nome_aba, rows="1000", cols="4")
            mapa_ids = {}

        linhas_processadas = []
        for linha in texto_colado.split('\n'):
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue
            partes = re.split(r'[\t\s]+', linha_limpa)
            codigo = partes[0].strip().upper()
            saldo = partes[1].strip() if len(partes) >= 2 else '0'
            row_id = mapa_ids.get(codigo) or str(uuid.uuid4())[:8]
            linhas_processadas.append([row_id, codigo, saldo, agora])

        if not linhas_processadas:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum dado válido encontrado!'})

        aba.update('A1', [["ID", "CODIGO", "SALDO", "ULTIMA ATUALIZACAO"]] + linhas_processadas)
        return jsonify({
            'sucesso': True,
            'mensagem': f"Saldo de {len(linhas_processadas)} item(ns) gravado em {nome_planilha_alvo} ({nome_aba})!"
        })
    except Exception as e:
        print(f"Erro em salvar_saldo_tipo: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro interno: {str(e)}'}), 500


@app.route('/salvar_codigos_contar_tipo', methods=['POST'])
def salvar_codigos_contar_tipo():
    try:
        dados = request.get_json() or {}
        tipo_raw = str(dados.get('tipo', '')).strip()
        data_mes_ano = str(dados.get('data', '')).strip()
        codigos = dados.get('codigos', [])

        tipo_norm = tipo_raw.lower()
        if not tipo_raw or tipo_norm not in PLANILHAS_TIPOS or not data_mes_ano or not codigos:
            return jsonify({'sucesso': False, 'mensagem': 'Informe tipo, mês/ano e códigos!'})

        nome_planilha_alvo = PLANILHAS_TIPOS[tipo_norm]
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha = gc.open(nome_planilha_alvo)
        try:
            aba = planilha.worksheet(data_mes_ano)
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=data_mes_ano, rows="1000", cols="8")
            aba.append_row(["ID", "Data", "Código", "Saldo Estoque", "Quantidade Contada", "Diferença", "Tipo", "Inventario Feito"])

        codigos_existentes = {
            str(reg.get('Código', '')).strip().upper()
            for reg in aba.get_all_records() if reg.get('Código')
        }

        linhas_adicionar = []
        for cod in codigos:
            cod_limpo = str(cod).strip().upper()
            if not cod_limpo or cod_limpo in codigos_existentes:
                continue
            linhas_adicionar.append([
                str(uuid.uuid4())[:8],
                data_mes_ano,
                cod_limpo,
                0,
                0,
                0,
                tipo_raw,
                'Não'
            ])
            codigos_existentes.add(cod_limpo)

        if linhas_adicionar:
            aba.append_rows(linhas_adicionar)
            return jsonify({
                'sucesso': True,
                'mensagem': f'{len(linhas_adicionar)} código(s) salvo(s) em {nome_planilha_alvo} ({data_mes_ano})!'
            })

        return jsonify({'sucesso': True, 'mensagem': 'Nenhum código novo para adicionar.'})
    except Exception as e:
        print(f"Erro em salvar_codigos_contar_tipo: {e}")
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'}), 500







if __name__ == '__main__':
    app.run(debug=True)
