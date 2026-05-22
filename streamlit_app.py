import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime
from difflib import get_close_matches

st.set_page_config(page_title="Hardware Inventory", layout="wide", page_icon="📦")

# --- CONNECT TO GOOGLE SHEETS ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- UTILITIES ---
def parse_dates(df):
    if not df.empty and 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True)
    return df

@st.cache_data(ttl=10)
def get_data():
    master = conn.read(worksheet="Product_Master")
    purchases = parse_dates(conn.read(worksheet="Purchases"))
    return master, purchases

@st.cache_data(ttl=10)
def get_code_mapping():
    try: return conn.read(worksheet="Code_Mapping")
    except: return pd.DataFrame(columns=["Item Code", "Item Name"])

@st.cache_data(ttl=10)
def get_learned_mappings():
    try: return conn.read(worksheet="Learned_Mappings")
    except: return pd.DataFrame(columns=["Billed_Description", "Matched_Item_Name"])

# Load Data
products_df, purchases_df = get_data()
mapping_df = get_code_mapping()
learned_df = get_learned_mappings()

# Dictionaries
code_dict = dict(zip(mapping_df['Item Code'].astype(str).str.strip(), mapping_df['Item Name'].astype(str).str.strip())) if not mapping_df.empty else {}
memory_dict = dict(zip(learned_df['Billed_Description'].astype(str).str.strip().str.upper(), learned_df['Matched_Item_Name'].astype(str).str.strip())) if not learned_df.empty else {}
stock_items = products_df['Item_Name'].dropna().unique().tolist()
stock_items_lower = {str(item).lower(): item for item in stock_items}

def find_best_match(description):
    desc_clean_upper = str(description).strip().upper()
    if desc_clean_upper in memory_dict: return memory_dict[desc_clean_upper]
    desc_lower = str(description).strip().lower()
    for key, val in stock_items_lower.items():
        if desc_lower in key or key in desc_lower: return val
    matches = get_close_matches(desc_lower, stock_items_lower.keys(), n=1, cutoff=0.5)
    return stock_items_lower[matches[0]] if matches else None

# --- UI TABS ---
st.title("📦 Hardware Inventory Management")
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🛒 Record Purchase", "📊 View Inventory", "📋 Masters & AI Memory", "📤 Bulk Upload Sales", "📝 Edit Purchase Bills", "📝 Edit Sales Bills"
])

# --- TAB 1: RECORD PURCHASE ---
with tab1:
    st.header("Enter New Purchase")
    selected_item = st.selectbox("Search Item", options=products_df['Item_Name'].unique(), index=None, key="record_select")
    if selected_item:
        item_details = products_df[products_df['Item_Name'] == selected_item].iloc[0]
        with st.form("purchase_form", clear_on_submit=True):
            bill_number = st.text_input("Bill Number")
            purchase_qty = st.number_input("Billed Purchase Qty", min_value=0.01)
            if st.form_submit_button("Record Entry"):
                new_record = pd.DataFrame([{
                    "Date": datetime.now().strftime("%d/%m/%Y"),
                    "Bill Number": bill_number, 
                    "Group": item_details['Group'],
                    "Item_Name": selected_item,
                    "Purchase Qty": purchase_qty,
                    "Purchase Unit": item_details['Purchase_Unit'],
                    "Stock Qty Added": purchase_qty,
                    "Stock Unit": item_details['Sales_Unit']
                }])
                conn.update(worksheet="Purchases", data=pd.concat([purchases_df, new_record], ignore_index=True))
                st.success("✅ Saved!")
                st.rerun()

# --- TAB 2: VIEW INVENTORY ---
with tab2:
    st.header("Stock & Ledger")
    inv = purchases_df.groupby(['Item_Name', 'Stock Unit'])['Stock Qty Added'].sum().reset_index()
    st.dataframe(inv, use_container_width=True)
    ledger_item = st.selectbox("View Ledger", options=products_df['Item_Name'].unique(), index=None, key="ledger_select")
    if ledger_item:
        ledger = purchases_df[purchases_df['Item_Name'] == ledger_item].sort_values('Date')
        ledger['Running Balance'] = ledger['Stock Qty Added'].cumsum()
        st.dataframe(ledger[['Date', 'Bill Number', 'Stock Qty Added', 'Running Balance']], use_container_width=True)

# --- TAB 3: MASTERS & AI MEMORY ---
with tab3:
    st.dataframe(products_df, use_container_width=True)
    if not learned_df.empty:
        st.dataframe(learned_df, use_container_width=True)
        if st.button("🧹 Optimize AI Memory"):
            clean_df = learned_df.drop_duplicates(subset=["Billed_Description"], keep="last")
            conn.update(worksheet="Learned_Mappings", data=clean_df)
            st.rerun()

# --- TAB 4: BULK UPLOAD SALES ---
with tab4:
    uploaded_file = st.file_uploader("Upload Sales File", type=['csv', 'xlsx'], key="file_upload")
    if uploaded_file and st.session_state.get("committed_file_name") != uploaded_file.name:
        if "processed_file_name" not in st.session_state or st.session_state.processed_file_name != uploaded_file.name:
            try:
                df_upload = pd.read_csv(uploaded_file, header=None) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file, header=None)
                df_upload[1] = df_upload[1].astype(str).str.strip()
                uploaded_bills_count = df_upload.groupby(1).size().to_dict()
                db_bills_count = purchases_df['Bill Number'].astype(str).str.strip().value_counts().to_dict() if not purchases_df.empty else {}
                duplicate_bills = [b for b, c in uploaded_bills_count.items() if b in db_bills_count and db_bills_count[b] == c]
                st.session_state.raw_upload_data = df_upload
                st.session_state.processed_file_name = uploaded_file.name
                if duplicate_bills:
                    st.session_state.resolving_duplicates = True
                    st.session_state.duplicate_bills = duplicate_bills
                else:
                    st.session_state.resolving_duplicates = False
                    st.session_state.df_to_process = df_upload
                st.rerun()
            except Exception as e: st.error(f"Error reading file: {e}")

        if st.session_state.get("resolving_duplicates"):
            with st.form("dup_form"):
                resolutions = {b: st.radio(f"Bill {b}", ["Skip", "Override", "Add"], key=f"dup_{b}") for b in st.session_state.duplicate_bills}
                if st.form_submit_button("Confirm"):
                    st.session_state.bills_to_delete = [b for b, act in resolutions.items() if act == "Override"]
                    st.session_state.df_to_process = st.session_state.raw_upload_data.copy()
                    st.session_state.resolving_duplicates = False
                    st.rerun()

        if not st.session_state.get("resolving_duplicates") and "df_to_process" in st.session_state and "auto_matched" not in st.session_state:
            auto_matched, unmatched = [], []
            for _, row in st.session_state.df_to_process.iterrows():
                raw_code = str(row[4]).strip() if pd.notna(row[4]) else ""
                other_desc = str(row[5]).strip() if pd.notna(row[5]) else ""
                merged = f"{code_dict.get(raw_code, raw_code)} - {other_desc}" if (code_dict.get(raw_code) or raw_code) else other_desc
                matched = find_best_match(merged)
                if matched:
                    item_details = products_df[products_df['Item_Name'] == matched].iloc[0]
                    auto_matched.append({"Date": row[0], "Bill Number": str(row[1]).strip(), "Group": item_details['Group'], "Item_Name": matched, "Purchase Qty": 0, "Purchase Unit": "-", "Stock Qty Added": -abs(float(row[2])), "Stock Unit": item_details['Sales_Unit'], "Display_Desc": merged})
                else:
                    unmatched.append({"Date": row[0], "Bill Number": str(row[1]).strip(), "Qty": row[2], "Description": merged})
            st.session_state.auto_matched, st.session_state.unmatched = auto_matched, unmatched
            st.rerun()

        if "auto_matched" in st.session_state:
            def commit_sales(new_learned=None):
                new_df = pd.DataFrame([{k:v for k,v in r.items() if k != 'Display_Desc'} for r in list(st.session_state.auto_matched)])
                current_p = purchases_df[~purchases_df['Bill Number'].astype(str).str.strip().isin(st.session_state.get("bills_to_delete", []))]
                conn.update(worksheet="Purchases", data=pd.concat([current_p, new_df], ignore_index=True))
                if new_learned:
                    conn.update(worksheet="Learned_Mappings", data=pd.concat([learned_df, pd.DataFrame(new_learned)], ignore_index=True).drop_duplicates(subset=["Billed_Description"], keep="last"))
                st.cache_data.clear()
                st.session_state.committed_file_name = st.session_state.processed_file_name
                st.rerun()
            if st.button("Commit Sales", key="commit_btn"): commit_sales()

# --- TAB 5/6: EDIT BILLS ---
def bill_editor(is_purchase, suffix):
    df_filtered = purchases_df[purchases_df['Purchase Qty'] > 0] if is_purchase else purchases_df[purchases_df['Stock Qty Added'] < 0]
    bill_list = sorted(df_filtered['Bill Number'].dropna().unique().astype(str))
    bill = st.selectbox(f"Select Bill ({suffix})", options=bill_list, index=None, key=f"sel_{suffix}")
    if bill:
        bill_data = purchases_df[purchases_df['Bill Number'].astype(str) == bill]
        edited = st.data_editor(bill_data, key=f"edit_{suffix}")
        if st.button("Save Changes", key=f"save_{suffix}"):
            final = pd.concat([purchases_df[~purchases_df['Bill Number'].astype(str).isin([bill])], edited])
            final['Date'] = pd.to_datetime(final['Date']).dt.strftime('%d/%m/%Y')
            conn.update(worksheet="Purchases", data=final)
            st.success("Saved!")
            st.rerun()

with tab5: bill_editor(True, "pur")
with tab6: bill_editor(False, "sal")
