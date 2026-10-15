from flask import Flask, render_template, request, jsonify
import gspread
import pandas as pd
from datetime import datetime
import os

app = Flask(__name__)

NOME_PLANILHA_CONTAGEM = 'contagem_semi_acabado'

def conectar_google_sheets():
    try:
        gc = gspread.service_account(filename='credenciais.json')
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao conectar ao Google Sheets: {e}")
        return None

# ==========================================
# ROTAS DE PÁGINAS (FRONTEND)
# ==========================================

@app.route('/contagem')
def index_contagem():
    return render_template('contagem_estoque.html')

@app.route('/api/obter_dados_iniciais')
def obter_dados_iniciais():
    """Retorna lista de códigos válidos da aba dados e o mês/ano atual padrão"""
    gc = conectar_google_sheets()
    if not gc:
        return jsonify({"erro": "Falha de conexão"}), 500
    
    try:
        planilha = gc.open(NOME_PLANILHA_CONTAGEM)
    except Exception:
        # Se a planilha não existir no Drive, cria-a automaticamente
        planilha = gc.create(NOME_PLANILHA_CONTAGEM)
        sheet1 = planilha.sheet1
        sheet1.update_title("dados")
        sheet1.append_row(["id", "codigo", "descricao"])

    # Carregar aba dados
    try:
        sheet_dados = planilha.worksheet("dados")
        registros_dados = sheet_dados.get_all_records()
    except Exception:
        registros_dados = []

    mes_ano_atual = datetime.now().strftime('%B_%Y').capitalize() # Ex: Outubro_2026
    
    return jsonify({
        "produtos_validos": registros_dados,
        "mes_ano_padrao": mes_ano_atual
    })

@app.route('/api/salvar_cadastro_produto', methods=['POST'])
def salvar_cadastro_produto():
    """Salva código e descrição na aba dados com ID automático"""
    data = request.json
    codigo = str(data.get('codigo', '')).strip()
    descricao = str(data.get('descricao', '')).strip()

    if not codigo or not descricao:
        return jsonify({"sucesso": False, "mensagem": "Código e Descrição são obrigatórios!"})

    gc = conectar_google_sheets()
    planilha = gc.open(NOME_PLANILHA_CONTAGEM)
    sheet_dados = planilha.worksheet("dados")
    
    registros = sheet_dados.get_all_records()
    
    # Verifica se já existe
    for r in registros:
        if str(r.get('codigo', '')).strip() == codigo:
            return jsonify({"sucesso": False, "mensagem": "Este código já está cadastrado!"})

    novo_id = f"item_{len(registros) + 1}_{int(datetime.now().timestamp())}"
    sheet_dados.append_row([novo_id, codigo, descricao])
    
    return jsonify({"sucesso": True, "mensagem": "Produto cadastrado com sucesso!"})

@app.route('/api/salvar_contagem', methods=['POST'])
def salvar_contagem():
    """Salva a contagem individual validando se o código existe na aba dados"""
    data = request.json
    codigo = str(data.get('codigo', '')).strip()
    contagem = float(data.get('contagem', 0))
    mes_ano = str(data.get('mes_ano', '')).strip()

    gc = conectar_google_sheets()
    planilha = gc.open(NOME_PLANILHA_CONTAGEM)

    # 1. Valida se o código existe na aba 'dados'
    sheet_dados = planilha.worksheet("dados")
    produtos = sheet_dados.get_all_records()
    produto_encontrado = next((p for p in produtos if str(p.get('codigo')).strip() == codigo), None)

    if not produto_encontrado:
        return jsonify({"sucesso": False, "mensagem": "Erro: Este código não está cadastrado na base de dados!"})

    # 2. Garante que a aba do mês existe na planilha
    try:
        sheet_mes = planilha.worksheet(mes_ano)
    except Exception:
        sheet_mes = planilha.add_worksheet(title=mes_ano, rows="1000", cols="10")
        sheet_mes.append_row(["id", "codigo", "contagem", "saldo", "diferenca"])

    # 3. Registo e cálculo de diferença
    saldo_estoque = 15.0 # Podes futuramente puxar o saldo guardado se aplicável
    diferenca = contagem - saldo_estoque
    novo_id = f"cont_{int(datetime.now().timestamp())}"

    sheet_mes.append_row([novo_id, codigo, contagem, saldo_estoque, diferenca])

    return jsonify({"sucesso": True, "mensagem": "Contagem salva com sucesso!"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)