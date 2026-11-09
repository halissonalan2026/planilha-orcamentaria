import sqlite3

def init_db():
    conn = sqlite3.connect('orcamento_obras.db')
    cursor = conn.cursor()

    # 1. Tabela de Clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            endereco TEXT
        )
    ''')

    # 2. Tabela de Obras / Projetos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS obras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER,
            nome_obra TEXT NOT NULL,
            valor_financiamento REAL DEFAULT 0.0,
            valor_entrada REAL DEFAULT 0.0,
            pdf_projeto TEXT,
            status TEXT DEFAULT 'Em Andamento',
            FOREIGN KEY (cliente_id) REFERENCES clientes (id)
        )
    ''')

    # 3. Tabela de Fornecedores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            material_principal TEXT
        )
    ''')

    # 4. Tabela de Materiais / Custos por Obra
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS materiais_obra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER,
            fornecedor_id INTEGER,
            descricao TEXT NOT NULL,
            quantidade REAL NOT NULL,
            valor_unitario REAL NOT NULL,
            valor_total REAL GENERATED ALWAYS AS (quantidade * valor_unitario) VIRTUAL,
            FOREIGN KEY (obra_id) REFERENCES obras (id),
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Banco de dados criado com sucesso!")

if __name__ == '__main__':
    init_db()