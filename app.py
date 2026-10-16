import json
import os
import pandas as pd
import gspread
import uuid
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

NOME_PLANILHA_PRODUTOS = 'contagem_semi_acabado'
NOME_PLANILHA_MOVIMENTACAO = 'MOVIMENTACAO_NOMUS'

produtos_cache = []
codigos_validos_set = set()

def conectar_google_sheets():
    try:
        if "GOOGLE_CREDENTIALS_JSON" in os.environ:
            creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
            gc = gspread.service_account_from_dict(creds_dict)
        else:
            gc = gspread.service_account(filename='credenciais.json')
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao autenticar com o Google Sheets: {e}")
        return None

def carregar_base_produtos():
    global produtos_cache, codigos_validos_set
    try:
        gc = conectar_google_sheets()
        if not gc:
            return
        
        planilha = gc.open(NOME_PLANILHA_PRODUTOS)
        sheet = planilha.worksheet('dados')
        dados = sheet.get_all_records()
        df = pd.DataFrame(dados)
        
        produtos_cache = []
        codigos_validos_set = set()
        for _, row in df.iterrows():
            # Aceita a coluna 'PRODUTO' ou 'CODIGO'
            codigo = str(row.get('PRODUTO', row.get('CODIGO', ''))).strip()
            descricao = str(row.get('DESCRICAO', '')).strip()
            if codigo and codigo != 'nan':
                produtos_cache.append({'codigo': codigo, 'descricao': descricao})
                codigos_validos_set.add(codigo)
                
        print(f"[INFO] {len(produtos_cache)} produtos carregados com sucesso da aba 'dados'!")
    except Exception as e:
        print(f"[ERRO] Falha ao carregar produtos: {e}")

# Carrega a base de produtos
carregar_base_produtos()

@app.route('/')
def index_contagem():
    return render_template('contagem_estoque.html')

@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])
    
    resultados = []
    for p in produtos_cache:
        if termo in p['codigo'].lower() or termo in p['descricao'].lower():
            resultados.append(p)
            if len(resultados) >= 15:
                break
    return jsonify(resultados)

@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data') # Exemplo: "10/2026"
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Mês/Ano ou itens não informados!'})

    try:
        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip()
            if cod_item not in codigos_validos_set:
                return jsonify({
                    'sucesso': False, 
                    'mensagem': f'Bloqueado! O código "{cod_item}" não existe na aba dados da planilha oficial.'
                })

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            sheet_mov = planilha_mov.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            sheet_mov = planilha_mov.add_worksheet(title=data_lote, rows="1000", cols="7")
            sheet_mov.append_row(["ID", "Data", "Código", "Quantidade", "Tipo", "Status", "Inventario Feito"])

        linhas_para_adicionar = []
        for item in itens:
            codigo = item.get('CODIGO', '')
            quantidade = int(item.get('QUANTIDADE', 1))
            tipo = item.get('TIPO', 'Contagem')
            status = 'Não'
            inventario_feito = 'Não'
            
            row_id = str(uuid.uuid4())[:8]
            linhas_para_adicionar.append([row_id, data_lote, codigo, quantidade, tipo, status, inventario_feito])

        sheet_mov.append_rows(linhas_para_adicionar)
        
        return jsonify({'sucesso': True, 'mensagem': f'Sucesso! Itens guardados na aba {data_lote} em MOVIMENTACAO_NOMUS.'})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao processar: {str(e)}'})

@app.route('/atualizar_status_lote_por_id', methods=['POST'])
def atualizar_status_lote_por_id():
    try:
        dados = request.get_json()
        if not dados:
            return jsonify({'sucesso': False, 'mensagem': 'Dados inválidos enviados.'}), 400
            
        data_consulta = str(dados.get('data', '')).strip()
        ids_selecionados = [str(i).strip() for i in dados.get('ids', [])]
        novo_status = str(dados.get('status', 'Não')).strip()
        
        if not data_consulta or not ids_selecionados:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum item selecionado.'}), 400
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            aba = planilha.worksheet(data_consulta)
        except Exception:
            return jsonify({'sucesso': False, 'mensagem': f'Aba da data {data_consulta} não encontrada.'}), 400
            
        linhas_planilha = aba.get_all_values()
        if not linhas_planilha or len(linhas_planilha) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'A planilha está vazia.'}), 400
            
        cabecalhos = [str(c).strip().upper() for c in linhas_planilha[0]]
        
        if "ID" not in cabecalhos or "STATUS" not in cabecalhos:
            return jsonify({'sucesso': False, 'mensagem': 'Colunas ID ou STATUS não encontradas na planilha.'}), 400
            
        col_id_idx = cabecalhos.index("ID") + 1
        col_status_idx = cabecalhos.index("STATUS") + 1
        
        cell_updates = []
        contador = 0
        
        for idx_l, linha in enumerate(linhas_planilha):
            if idx_l == 0:
                continue
                
            id_celula = str(linha[col_id_idx - 1]).strip() if len(linha) >= col_id_idx else ""
            
            if id_celula in ids_selecionados:
                cell_address = gspread.utils.rowcol_to_a1(idx_l + 1, col_status_idx)
                cell_updates.append({
                    'range': cell_address,
                    'values': [[novo_status]]
                })
                contador += 1
                
        if cell_updates:
            aba.batch_update(cell_updates)
                
        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado com sucesso em {contador} item(ns)!'})
        
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro interno no servidor: {str(e)}'}), 500

@app.route('/consultar_movimentacao', methods=['GET'])
def consultar_movimentacao():
    data_consulta = request.args.get('data', '').strip()
    if not data_consulta:
        return jsonify({'sucesso': False, 'mensagem': 'Mês/Ano não informado!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            sheet_mov = planilha_mov.worksheet(data_consulta)
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': True, 'itens': [], 'mensagem': f'Nenhuma contagem encontrada para {data_consulta}.'})

        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': True, 'itens': [], 'mensagem': f'Nenhuma contagem encontrada para {data_consulta}.'})

        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        tem_id = "ID" in cabecalhos

        itens_formatados = []
        for idx in range(1, len(linhas)):
            row = linhas[idx]
            if not any(row):
                continue
            
            if tem_id:
                row_id = row[cabecalhos.index("ID")] if "ID" in cabecalhos and len(row) > cabecalhos.index("ID") else f"linha_{idx+1}"
                data_val = row[cabecalhos.index("DATA")] if "DATA" in cabecalhos and len(row) > cabecalhos.index("DATA") else data_consulta
                codigo_val = row[cabecalhos.index("CÓDIGO")] if "CÓDIGO" in cabecalhos and len(row) > cabecalhos.index("CÓDIGO") else (row[cabecalhos.index("CODIGO")] if "CODIGO" in cabecalhos and len(row) > cabecalhos.index("CODIGO") else "")
                qtd_val = row[cabecalhos.index("QUANTIDADE")] if "QUANTIDADE" in cabecalhos and len(row) > cabecalhos.index("QUANTIDADE") else 1
                tipo_val = row[cabecalhos.index("TIPO")] if "TIPO" in cabecalhos and len(row) > cabecalhos.index("TIPO") else "Contagem"
                status_val = row[cabecalhos.index("STATUS")] if "STATUS" in cabecalhos and len(row) > cabecalhos.index("STATUS") else "Não"
            else:
                row_id = f"fallback_{idx+1}"
                data_val = row[0] if len(row) > 0 else data_consulta
                codigo_val = row[1] if len(row) > 1 else ""
                qtd_val = row[2] if len(row) > 2 else 1
                tipo_val = row[3] if len(row) > 3 else "Contagem"
                status_val = row[4] if len(row) > 4 else "Não"

            itens_formatados.append({
                "ID": row_id,
                "Data": data_val,
                "Código": codigo_val,
                "Quantidade": qtd_val,
                "Tipo": tipo_val,
                "Status": status_val
            })

        return jsonify({'sucesso': True, 'itens': itens_formatados})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao consultar: {str(e)}'})

@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()

    if not data_mov or not row_id:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou ID não informados!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        
        if "ID" not in cabecalhos:
            return jsonify({'sucesso': False, 'mensagem': 'Coluna ID não encontrada na planilha.'})
            
        col_id_idx = cabecalhos.index("ID") + 1

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            val_id = str(linhas[idx][col_id_idx - 1]).strip() if len(linhas[idx]) >= col_id_idx else ""
            if val_id == row_id:
                linha_encontrada = idx + 1
                break

        if linha_encontrada != -1:
            sheet_mov.delete_rows(linha_encontrada)
            return jsonify({'sucesso': True, 'mensagem': 'Item excluído com sucesso da planilha!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado pelo ID na planilha.'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao excluir: {str(e)}'})

if __name__ == '__main__':
    app.run(debug=True)
