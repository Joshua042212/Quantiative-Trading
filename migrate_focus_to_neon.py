#!/usr/bin/env python
"""
Migrate focus tables from local DB to Neon
"""
from sqlalchemy import create_engine, text
import getpass

# 提示输入本地DB密码
local_password = getpass.getpass("输入本地 PostgreSQL 密码 (通常为空，直接回车): ") or ""

# 本地连接
local_url = f'postgresql://postgres:{local_password}@localhost:5432/stock_db'
local_engine = create_engine(local_url)
local_db = local_engine.connect()

# Neon 连接 - 改成你的 Neon 密码
from sqlalchemy import create_engine
neon_password = input("输入 Neon 密码: ")
neon_url = f"postgresql://postgres:{neon_password}@pg.neon.tech:5432/neondb"
neon_engine = create_engine(neon_url)

tables = [
    'raw_kline_yfinance_focus',
    'monthly_revenue_focus', 
    'company_info_focus',
    'company_financials_focus'
]

print("开始迁移...")

for table in tables:
    print(f"\n迁移 {table}...")
    
    # 获取列名
    result = local_db.execute(text(f"SELECT * FROM {table} LIMIT 0"))
    cols = [desc for desc in result.keys()]
    col_str = ', '.join(cols)
    
    # 计算总数
    total = local_db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    print(f"  总行数: {total}")
    
    # 分批迁移 (每批1000行)
    batch_size = 1000
    migrated = 0
    
    with neon_engine.connect() as neon_conn:
        for offset in range(0, total, batch_size):
            query = f"SELECT {col_str} FROM {table} ORDER BY id LIMIT {batch_size} OFFSET {offset}"
            rows = local_db.execute(text(query)).fetchall()
            
            for row in rows:
                # 构建INSERT语句
                values = []
                for val in row:
                    if val is None:
                        values.append('NULL')
                    elif isinstance(val, str):
                        values.append(f"'{val.replace(chr(39), chr(39)+chr(39))}'")
                    elif isinstance(val, bool):
                        values.append('TRUE' if val else 'FALSE')
                    else:
                        values.append(str(val))
                
                val_str = ', '.join(values)
                insert_sql = f"INSERT INTO {table} ({col_str}) VALUES ({val_str})"
                neon_conn.execute(text(insert_sql))
            
            migrated += len(rows)
            print(f"  已迁移: {migrated}/{total}")
            neon_conn.commit()
    
    print(f"  ✓ {table} 完成")

print("\n✓ 所有表迁移完成！")
local_db.close()
neon_engine.dispose()
