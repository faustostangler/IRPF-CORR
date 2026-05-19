import yaml
from datetime import date, datetime
import sys
from pathlib import Path

sys.path.append(str(Path("src").resolve()))
from irpf_corr.ptax import get_ptax

with open('docs/notas/notas.yaml', 'r') as f:
    data = yaml.safe_load(f)

print("LINK NOTA\tDATA\tCUSTO TOTAL NOTA\tPAPEL\tOP\tQTD\tPREÇO R$\tPTAX")

for note in data:
    d = note.get('date', '')
    
    parsed_date = None
    if isinstance(d, date) and not isinstance(d, datetime):
        parsed_date = d
        date_fmt = d.strftime('%d/%m/%Y')
    elif isinstance(d, str):
        try:
            dt = datetime.strptime(d, '%Y-%m-%d')
            parsed_date = dt.date()
            date_fmt = dt.strftime('%d/%m/%Y')
        except ValueError:
            try:
                dt = datetime.strptime(d, '%d/%m/%Y')
                parsed_date = dt.date()
                date_fmt = d
            except ValueError:
                date_fmt = d
    else:
        date_fmt = str(d)
        
    custo_total = (
        note.get('brokerage_fee', 0.0) +
        note.get('settlement_fee', 0.0) +
        note.get('exchange_fee', 0.0) +
        note.get('iss_tax', 0.0) +
        note.get('irrf_tax', 0.0)
    )
    
    custo_fmt = f"{custo_total:.2f}".replace('.', ',')
    
    currency = note.get('currency', 'BRL')
    if currency == 'USD':
        # Buscando o PTAX com a data do objeto (precisamos garantir que é date)
        if parsed_date:
            ptax_val = get_ptax(parsed_date)
            ptax_fmt = f"{ptax_val:.4f}".replace('.', ',')
        else:
            ptax_fmt = "ERRO_DATA"
    else:
        ptax_fmt = "1,0000"
    
    for trade in note.get('trades', []):
        ticker = trade.get('ticker', '')
        op = 'C' if trade.get('direction') == 'BUY' else 'V'
        qtd = trade.get('quantity', 0)
        preco = trade.get('unit_price', 0.0)
        
        # Format quantity
        if isinstance(qtd, float) or '.' in str(qtd):
            qtd_str = str(qtd).replace('.', ',')
        else:
            qtd_str = str(qtd)
            
        # Format price
        if '.' in str(preco):
            s = f"{float(preco):.4f}"
            while s.endswith('0') and len(s) > s.find('.') + 3:
                s = s[:-1]
            if s.endswith('00'):
                s = s[:-1]
            preco_fmt = s.replace('.', ',')
        else:
            preco_fmt = str(preco).replace('.', ',') + ",00"
            
        print(f"-\t{date_fmt}\t{custo_fmt}\t{ticker}\t{op}\t{qtd_str}\t{preco_fmt}\t{ptax_fmt}")
