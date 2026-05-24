import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime
from difflib import get_close_matches
import re

st.set_page_config(page_title="Hardware Inventory", layout="wide", page_icon="📦")

# --- CONNECT TO GOOGLE SHEETS ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- HELPER TO FORCE DD/MM/YYYY FORMAT ON SAVES ---
def save_purchases(df_to_save):
    df_save = df_to_save.copy()
    if 'Date' in df_save.columns:
        df_save['Date'] = pd.to_datetime(df_save['Date'], dayfirst=True, errors='coerce').dt.strftime('%d/%m/%Y')
    conn.update(worksheet="Purchases", data=df_save)

@st.cache_data(ttl=10)
def get_product_master():
    return conn.read(worksheet="Product_Master")

@st.cache_data(ttl=10)
def get_purchases():
    return conn.read(worksheet="Purchases")

@st.cache_data(ttl=10)
def get_code_mapping():
    try:
        return conn.read(worksheet="Code_Mapping")
    except:
        return pd.DataFrame(columns=["Item Code", "Item Name", "Units"])

@st.cache_data(ttl=10)
def get_learned_mappings():
    try:
        return conn.read(worksheet="Learned_Mappings")
    except:
        return pd.DataFrame(columns=["Billed_Description", "Matched_Item_Name"])

products_df = get_product_master()
purchases_df = get_purchases()
mapping_df = get_code_mapping()
learned_df = get_learned_mappings()

# 1. Create the Product Code & Unit translation dictionaries
code_dict = {}
unit_dict = {}
if not mapping_df.empty:
    if len(mapping_df.columns) >= 2:
        code_dict = dict(zip(mapping_df.iloc[:, 0].astype(str).str.strip(), mapping_df.iloc[:, 1].astype(str).str.strip()))
    if len(mapping_df.columns) >= 3:
        unit_dict = dict(zip(mapping_df.iloc[:, 0].astype(str).str.strip(), mapping_df.iloc[:, 2].astype(str).str.strip()))

# 2. Create the AI Memory dictionary
memory_dict = {}
if not learned_df.empty and 'Billed_Description' in learned_df.columns and 'Matched_Item_Name' in learned_df.columns:
    memory_dict = dict(zip(learned_df['Billed_Description'].astype(str).str.strip().str.upper(), learned_df['Matched_Item_Name'].astype(str).str.strip()))

# Pre-process stock items for fuzzy matching
stock_items = products_df['Item_Name'].dropna().unique().tolist()
stock_items_lower = {str(item).lower(): item for item in stock_items}

# --- ADVANCED WORD-BY-WORD FUZZY AI LOGIC ---
def find_best_match(description):
    # Step 1: Check AI Memory Bank First (Exact Override)
    desc_clean_upper = str(description).strip().upper()
    if desc_clean_upper in memory_dict:
        return memory_dict[desc_clean_upper]
    
    # Pre-process the string
    desc_lower = str(description).strip().lower()
    
    # Step 2: Custom Parameter Rules
    desc_lower = re.sub(r'\bred\b', 'maroon', desc_lower)
    desc_lower = re.sub(r'\bms\s+sq\s+rod\b', 'square rod', desc_lower)
    desc_lower = re.sub(r'\bms\s+square\s+rod\b', 'square rod', desc_lower)
    desc_lower = re.sub(r'\bms\s+plain\s+rod\b', 'plain rod', desc_lower)
    desc_lower = re.sub(r'\bms\s+sq\s+pipe\b', 'square pipe', desc_lower)
    desc_lower = re.sub(r'\bms\s+square\s+pipe\b', 'square pipe', desc_lower)
    desc_lower = re.sub(r'\bfibre\s+corrugated\s+sheet\b', 'fibre jasta', desc_lower)
    
    if re.search(r'\bms\b', desc_lower) and re.search(r'\bround\b', desc_lower) and re.search(r'\bpipe\b', desc_lower):
        desc_lower = re.sub(r'\bms\b', 'black pipe', desc_lower)
        desc_lower = re.sub(r'\bround\b', '', desc_lower)
        desc_lower = re.sub(r'\bpipe\b', '', desc_lower)
        desc_lower = " ".join(desc_lower.split())

    # --- NEW STEP 3 & 4: ENTIRE LIST SCORING ---
    best_match = None
    highest_score = 0
    desc_words = set(desc_lower.split())
    
    for key, val in stock_items_lower.items():
        # 1. Check for Absolute Exact Match First (Score: 2.0)
        if desc_lower == key:
            return val 
            
        # 2. Check for Substring Match (e.g. "Pipe" is inside "MS Pipe") (Score: 1.5)
        # We penalize it slightly based on how much extra text there is, so the closest substring wins
        if desc_lower in key or key in desc_lower:
            length_diff = abs(len(desc_lower) - len(key))
            sub_score = 1.5 - (length_diff * 0.01) # Closer in length = higher score
            if sub_score > highest_score:
                highest_score = sub_score
                best_match = val
                continue
                
        # 3. Word-by-Word Matrix Scoring (Score: 0.0 to 1.0)
        if desc_words:
            key_words = set(key.split())
            if not key_words: continue
            
            score = 0
            for d_word in desc_words:
                if d_word in key_words:
                    score += 1 
                elif get_close_matches(d_word, key_words, n=1, cutoff=0.8):
                    score += 0.8 
            
            effective_desc_len = len(desc_words)
            if len(desc_words) > len(key_words):
                effective_desc_len -= 1  
                
            denominator = max(len(key_words), effective_desc_len)
            match_ratio = score / denominator if denominator > 0 else 0
            
            if match_ratio > highest_score:
                highest_score = match_ratio
                best_match = val

    # If ANY of the methods above found a match with at least 80% confidence, return the absolute best one
    if highest_score >= 0.8:
        return best_match
            
    # Step 5: Absolute Fallback (Standard Fuzzy Match on the whole string, strict 80% cutoff)
    matches = get_close_matches(desc_lower, stock_items_lower.keys(), n=1, cutoff=0.8)
    if matches:
        return stock_items_lower[matches[0]]
        
    return None
st.title("📦 Hardware Inventory Management")
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🛒 Record Purchase", "📊 View Inventory", "📋 Masters & AI Memory", "📤 Bulk Upload Sales", "📝 Edit Purchase Bills", "📝 Edit Sales Bills"
])

# --- TAB 1: RECORD PURCHASE ---
with tab1:
    st.header("Enter New Purchase / Goods Receipt")
    items = products_df['Item_Name'].dropna().unique()
    selected_item = st.selectbox("Search and Select Item", options=items, index=None, placeholder="Click here to type...")

    if selected_item:
        item_details = products_df[products_df['Item_Name'] == selected_item].iloc[0]
        p_unit = item_details['Purchase_Unit']
        s_unit = item_details['Sales_Unit']
        group = item_details['Group'] 
        
        with st.form("purchase_form", clear_on_submit=True):
            st.subheader(f"Selected: {selected_item}")
            bill_number = st.text_input("Bill / Invoice Number", placeholder="Enter Bill Number...")
            
            c1, c2 = st.columns(2)
            with c1:
                purchase_qty = st.number_input(f"Billed Purchase Qty ({p_unit})", min_value=0.01, step=1.0, value=None)
            with c2:
                if p_unit != s_unit:
                    st.info(f"Conversion: Bought in {p_unit}, Stocked in {s_unit}")
                    stock_qty = st.number_input(f"Physical Stock Received ({s_unit})", min_value=1.0, step=1.0, value=None)
                else:
                    stock_qty = purchase_qty
                    st.info(f"Units match. Stock added will be exactly the purchase quantity ({s_unit}).")
            
            if st.form_submit_button("Record Entry", type="primary"):
                if purchase_qty and (p_unit == s_unit or stock_qty):
                    final_stock = stock_qty if p_unit != s_unit else purchase_qty
                    new_record = pd.DataFrame([{
                        "Date": datetime.now().strftime("%d/%m/%Y"),
                        "Bill Number": bill_number, 
                        "Group": group,
                        "Item_Name": selected_item,
                        "Purchase Qty": purchase_qty,
                        "Purchase Unit": p_unit,
                        "Stock Qty Added": final_stock, 
                        "Stock Unit": s_unit
                    }])
                    updated_purchases = pd.concat([purchases_df, new_record], ignore_index=True)
                    save_purchases(updated_purchases)
                    st.cache_data.clear()
                    st.success(f"✅ Saved! Added {final_stock} {s_unit} to inventory under Bill: {bill_number}")

# --- TAB 2: VIEW INVENTORY & LEDGER ---
with tab2:
    st.header("Live Stock Levels & Ledger")
    
    st.subheader("Stock Summary Report")
    summary_items = st.multiselect("Select Item(s) to view (Leave blank for all)", options=products_df['Item_Name'].dropna().unique())
    
    if not purchases_df.empty:
        inventory_summary = purchases_df.groupby(['Group', 'Item_Name', 'Stock Unit'])['Stock Qty Added'].sum().reset_index()
        inventory_summary.rename(columns={'Stock Qty Added': 'Total Stock on Hand'}, inplace=True)
        
        if summary_items:
            inventory_summary = inventory_summary[inventory_summary['Item_Name'].isin(summary_items)]
            
        st.dataframe(inventory_summary, use_container_width=True, hide_index=True)
        
        st.divider()
        st.subheader("Item Stock Ledger")
        ledger_item = st.selectbox("Select a particular item to view its ledger", options=products_df['Item_Name'].dropna().unique(), index=None)
        
        if ledger_item:
            ledger = purchases_df[purchases_df['Item_Name'] == ledger_item].copy()
            ledger['Date_Parsed'] = pd.to_datetime(ledger['Date'], dayfirst=True, errors='coerce')
            ledger = ledger.sort_values('Date_Parsed')
            ledger['Running Balance'] = ledger['Stock Qty Added'].cumsum()
            
            st.dataframe(ledger[['Date', 'Bill Number', 'Purchase Qty', 'Stock Qty Added', 'Running Balance']], use_container_width=True, hide_index=True)
    else:
        st.write("No inventory data found.")

# --- TAB 3: PRODUCT MASTER & AI MEMORY ---
with tab3:
    st.header("Database & AI Memory")
    
    st.subheader("1. Base Product Master")
    st.dataframe(products_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    st.subheader("2. AI Learned Mappings (Memory Bank)")
    st.write("The AI automatically saves rules when you manually match items. It uses these to get smarter over time.")
    
    if not learned_df.empty:
        st.dataframe(learned_df, use_container_width=True, hide_index=True)
        
        if st.button("🧹 Optimize & Clean Duplicates from AI Memory"):
            clean_df = learned_df.copy()
            clean_df['Billed_Description'] = clean_df['Billed_Description'].astype(str).str.strip().str.upper()
            clean_df = clean_df.drop_duplicates(subset=["Billed_Description"], keep="last")
            conn.update(worksheet="Learned_Mappings", data=clean_df)
            st.cache_data.clear()
            st.success("✅ AI Memory Optimized! All duplicate formatting variations have been removed.")
            st.rerun()
    else:
        st.info("The AI Memory is currently empty. It will learn when you manually map unmatched items!")

# --- TAB 4: BULK UPLOAD SALES ---
with tab4:
    st.header("Upload Sales Data (Deduct from Inventory)")
    uploaded_file = st.file_uploader("Upload Sales File (No Headers)", type=['csv', 'xlsx'])
    
    if uploaded_file is not None:
        
        if st.session_state.get("committed_file_name") == uploaded_file.name:
            st.success("🎉 Database updated successfully! Please clear the file above (click the 'X') to upload a new one.")
        else:
            # 1. INITIAL LOAD & DUPLICATE CHECK
            if "processed_file_name" not in st.session_state or st.session_state.processed_file_name != uploaded_file.name:
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df_upload = pd.read_csv(uploaded_file, header=None)
                    else:
                        df_upload = pd.read_excel(uploaded_file, header=None)
                    
                    df_upload[1] = df_upload[1].astype(str).str.strip()
                    uploaded_bills_count = df_upload.groupby(1).size().to_dict()
                    
                    db_bills_count = {}
                    if not purchases_df.empty and 'Bill Number' in purchases_df.columns:
                        db_bills_count = purchases_df['Bill Number'].astype(str).str.strip().value_counts().to_dict()
                    
                    duplicate_bills = []
                    for b_no, count in uploaded_bills_count.items():
                        if b_no in db_bills_count and db_bills_count[b_no] == count:
                            duplicate_bills.append(b_no)
                    
                    st.session_state.raw_upload_data = df_upload
                    st.session_state.processed_file_name = uploaded_file.name
                    st.session_state.bills_to_delete = [] 
                    
                    if duplicate_bills:
                        st.session_state.resolving_duplicates = True
                        st.session_state.duplicate_bills = duplicate_bills
                    else:
                        st.session_state.resolving_duplicates = False
                        st.session_state.df_to_process = df_upload
                    
                    st.rerun()
                except Exception as e:
                    st.error(f"Error reading file: {e}")

            # 2. DUPLICATE RESOLUTION UI
            if st.session_state.get("resolving_duplicates", False):
                st.warning(f"⚠️ Found {len(st.session_state.duplicate_bills)} duplicate bill(s) matching exactly in the database.")
                
                with st.form("resolve_duplicates_form"):
                    resolutions = {}
                    for bill in st.session_state.duplicate_bills:
                        resolutions[bill] = st.radio(
                            f"Bill Number: {bill}",
                            options=["Skip (Do not import)", "Override (Replace old bill)", "Add Duplicate (Keep both)"],
                            key=f"res_{bill}"
                        )
                    
                    if st.form_submit_button("Confirm Resolutions", type="primary"):
                        df_to_process = st.session_state.raw_upload_data.copy()
                        bills_to_delete = []
                        
                        for bill, action in resolutions.items():
                            if "Skip" in action:
                                df_to_process = df_to_process[df_to_process[1] != bill]
                            elif "Override" in action:
                                bills_to_delete.append(bill)
                        
                        st.session_state.bills_to_delete = bills_to_delete
                        st.session_state.df_to_process = df_to_process
                        st.session_state.resolving_duplicates = False
                        st.rerun()

            # 3. FUZZY MATCHING & UNIT COMPARISON
            if not st.session_state.get("resolving_duplicates", False) and "auto_matched" not in st.session_state and "df_to_process" in st.session_state:
                df_to_process = st.session_state.df_to_process
                auto_matched_records = []
                unmatched_raw_records = []
                
                for index, row in df_to_process.iterrows():
                    date_val = row[0]
                    bill_val = str(row[1]).strip()
                    qty_val = float(row[2])
                    
                    sales_unit = str(row[9]).strip() if len(row) > 9 and pd.notna(row[9]) else ""
                    raw_item_code = str(row[4]).strip() if len(row) > 4 and pd.notna(row[4]) else ""
                    other_desc = str(row[5]).strip() if len(row) > 5 and pd.notna(row[5]) else ""
                    
                    mapped_name = code_dict.get(raw_item_code, raw_item_code)
                    sku_unit = unit_dict.get(raw_item_code, "")
                    
                    if sales_unit and sku_unit:
                        if sales_unit.lower() == sku_unit.lower():
                            unit_check = f"✅ {sales_unit}"
                        else:
                            unit_check = f"⚠️ File: {sales_unit} | SKU: {sku_unit}"
                    else:
                        unit_check = f"{sales_unit}" if sales_unit else f"{sku_unit}"
                    
                    if mapped_name and mapped_name.lower() != 'nan':
                        merged_description = f"{mapped_name} - {other_desc}".strip(" -")
                    else:
                        merged_description = other_desc
                    
                    matched_item = find_best_match(merged_description)
                    
                    if matched_item:
                        item_details = products_df[products_df['Item_Name'] == matched_item].iloc[0]
                        auto_matched_records.append({
                            "Date": date_val,
                            "Bill Number": bill_val, 
                            "Group": item_details['Group'], 
                            "Item_Name": matched_item,
                            "Purchase Qty": 0, 
                            "Purchase Unit": "-", 
                            "Stock Qty Added": -abs(qty_val), 
                            "Stock Unit": item_details['Sales_Unit'], 
                            "Original Billed Data": merged_description,
                            "Unit Check": unit_check, 
                            "Display_Desc": merged_description
                        })
                    else:
                        unmatched_raw_records.append({
                            "Date": date_val, 
                            "Bill Number": bill_val, 
                            "Qty": qty_val, 
                            "Description": merged_description,
                            "Original Billed Data": merged_description,
                            "Unit Check": unit_check 
                        })
                
                st.session_state.auto_matched = auto_matched_records
                st.session_state.unmatched = unmatched_raw_records
                st.rerun()

            # 4. FINAL REVIEW & COMMIT UI
            if "auto_matched" in st.session_state:
                auto_matched = st.session_state.auto_matched
                unmatched = st.session_state.unmatched
                
                # --- Unified Safe Commit Function taking processed records ---
                def commit_sales_to_db(records_to_save, new_learned=None):
                    new_records_df = pd.DataFrame(records_to_save)
                    current_purchases = purchases_df.copy()
                    bills_to_delete = st.session_state.get("bills_to_delete", [])
                    
                    if bills_to_delete and not current_purchases.empty and 'Bill Number' in current_purchases.columns:
                        current_purchases = current_purchases[~current_purchases['Bill Number'].astype(str).str.strip().isin(bills_to_delete)]
                    
                    if not new_records_df.empty or bills_to_delete:
                        if not new_records_df.empty:
                            updated_purchases = pd.concat([current_purchases, new_records_df], ignore_index=True)
                        else:
                            updated_purchases = current_purchases
                        save_purchases(updated_purchases)
                    
                    if new_learned:
                        new_rules_df = pd.DataFrame(new_learned)
                        updated_learnings = pd.concat([learned_df, new_rules_df], ignore_index=True)
                        updated_learnings['Billed_Description'] = updated_learnings['Billed_Description'].astype(str).str.strip().str.upper()
                        updated_learnings = updated_learnings.drop_duplicates(subset=["Billed_Description"], keep="last")
                        conn.update(worksheet="Learned_Mappings", data=updated_learnings)
                    
                    st.cache_data.clear()
                    st.session_state.committed_file_name = st.session_state.processed_file_name
                    
                    keys_to_clear = ['auto_matched', 'unmatched', 'raw_upload_data', 'resolving_duplicates', 'df_to_process', 'bills_to_delete']
                    for key in keys_to_clear:
                        if key in st.session_state:
                            del st.session_state[key]
                            
                    st.rerun()

                # --- Editable Auto-Matched DataFrame ---
                if auto_matched:
                    st.success(f"✅ Automatically matched {len(auto_matched)} items.")
                    st.write("✏️ **Review and override any incorrect automatic matches below:**")
                    display_df = pd.DataFrame(auto_matched).drop(columns=['Display_Desc'], errors='ignore')
                    
                    edited_auto_df = st.data_editor(
                        display_df,
                        column_config={
                            "Item_Name": st.column_config.SelectboxColumn(
                                "Item_Name (Editable)",
                                help="Select the correct master product to override the AI",
                                options=stock_items,
                                required=True
                            )
                        },
                        use_container_width=True,
                        key="auto_match_editor"
                    )
                else:
                    edited_auto_df = pd.DataFrame()

                if unmatched:
                    st.warning(f"⚠️ {len(unmatched)} items could not be matched automatically.")
                    with st.form("manual_mapping_form"):
                        manual_selections = []
                        h1, h2, h3, h4, h5 = st.columns([1, 2, 0.8, 1.2, 2.5])
                        h1.write("**Bill No**")
                        h2.write("**Billed Description**")
                        h3.write("**Qty**")
                        h4.write("**Unit Check**")
                        h5.write("**Match to Master Product**")
                        st.divider()
                        
                        for idx, un_row in enumerate(unmatched):
                            c1, c2, c3, c4, c5 = st.columns([1, 2, 0.8, 1.2, 2.5])
                            with c1: st.write(un_row['Bill Number'])
                            with c2: st.write(un_row['Original Billed Data'])
                            with c3: st.write(un_row['Qty'])
                            with c4: st.write(un_row.get('Unit Check', '-'))
                            with c5:
                                selected = st.selectbox("Match", options=["-- Skip / Do Not Import --"] + stock_items, key=f"un_{idx}", label_visibility="collapsed")
                            manual_selections.append((un_row, selected))
                            
                        st.write("")
                        if st.form_submit_button("Confirm Manual Matches & Commit ALL Sales", type="primary"):
                            final_records_to_commit = []
                            new_learned_rules = [] 
                            
                            # 1. Process Auto-Matched edits
                            if not edited_auto_df.empty:
                                for idx, row in edited_auto_df.iterrows():
                                    orig_row = auto_matched[idx]
                                    current_item = row['Item_Name']
                                    item_details = products_df[products_df['Item_Name'] == current_item].iloc[0]
                                    
                                    final_records_to_commit.append({
                                        "Date": row['Date'], 
                                        "Bill Number": row['Bill Number'], 
                                        "Group": item_details['Group'], 
                                        "Item_Name": current_item,
                                        "Purchase Qty": 0, 
                                        "Purchase Unit": "-", 
                                        "Stock Qty Added": row['Stock Qty Added'], 
                                        "Stock Unit": item_details['Sales_Unit']
                                    })
                                    
                                    if current_item != orig_row['Item_Name']:
                                        new_learned_rules.append({
                                            "Billed_Description": str(orig_row['Display_Desc']).strip().upper(),
                                            "Matched_Item_Name": current_item
                                        })
                            
                            # 2. Process Manual matches
                            for un_row, selected_item in manual_selections:
                                if selected_item != "-- Skip / Do Not Import --":
                                    item_details = products_df[products_df['Item_Name'] == selected_item].iloc[0]
                                    
                                    final_records_to_commit.append({
                                        "Date": un_row['Date'], 
                                        "Bill Number": un_row['Bill Number'], 
                                        "Group": item_details['Group'], 
                                        "Item_Name": selected_item,
                                        "Purchase Qty": 0, 
                                        "Purchase Unit": "-", 
                                        "Stock Qty Added": -abs(un_row['Qty']), 
                                        "Stock Unit": item_details['Sales_Unit']
                                    })
                                    
                                    new_learned_rules.append({
                                        "Billed_Description": str(un_row['Description']).strip().upper(),
                                        "Matched_Item_Name": selected_item
                                    })
                            
                            if final_records_to_commit or st.session_state.get("bills_to_delete"):
                                commit_sales_to_db(final_records_to_commit, new_learned_rules)
                else:
                    if st.button("Commit Sales to Database", type="primary"):
                        final_records_to_commit = []
                        new_learned_rules = []
                        
                        # Process Auto-Matched edits
                        if not edited_auto_df.empty:
                            for idx, row in edited_auto_df.iterrows():
                                orig_row = auto_matched[idx]
                                current_item = row['Item_Name']
                                item_details = products_df[products_df['Item_Name'] == current_item].iloc[0]
                                
                                final_records_to_commit.append({
                                    "Date": row['Date'], 
                                    "Bill Number": row['Bill Number'], 
                                    "Group": item_details['Group'], 
                                    "Item_Name": current_item,
                                    "Purchase Qty": 0, 
                                    "Purchase Unit": "-", 
                                    "Stock Qty Added": row['Stock Qty Added'], 
                                    "Stock Unit": item_details['Sales_Unit']
                                })
                                
                                if current_item != orig_row['Item_Name']:
                                    new_learned_rules.append({
                                        "Billed_Description": str(orig_row['Display_Desc']).strip().upper(),
                                        "Matched_Item_Name": current_item
                                    })
                                    
                        if final_records_to_commit or st.session_state.get("bills_to_delete"):
                            commit_sales_to_db(final_records_to_commit, new_learned_rules)

    else:
        keys_to_clear = ['auto_matched', 'unmatched', 'processed_file_name', 'raw_upload_data', 'resolving_duplicates', 'df_to_process', 'bills_to_delete', 'committed_file_name']
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]

# --- TABS 5 & 6: EDIT PURCHASE / SALES BILLS ---
def bill_editor(is_purchase, suffix):
    st.header(f"Edit {'Purchase' if is_purchase else 'Sales'} Bills")
    
    if purchases_df.empty:
        st.info("No records found.")
        return
        
    if is_purchase:
        df_filtered = purchases_df[pd.to_numeric(purchases_df['Purchase Qty'], errors='coerce') > 0].copy()
    else:
        df_filtered = purchases_df[pd.to_numeric(purchases_df['Stock Qty Added'], errors='coerce') < 0].copy()

    if df_filtered.empty:
        st.info(f"No {'purchase' if is_purchase else 'sales'} records found.")
        return

    df_filtered['Date_Str'] = pd.to_datetime(df_filtered['Date'], dayfirst=True, errors='coerce').dt.strftime('%d/%m/%Y')
    df_filtered['Bill_Label'] = df_filtered['Bill Number'].astype(str).str.strip() + " (Date: " + df_filtered['Date_Str'].astype(str) + ")"
    
    bill_list = sorted(df_filtered['Bill_Label'].dropna().unique())
    selected_label = st.selectbox(f"Select Bill to Edit", options=bill_list, index=None, key=f"sel_{suffix}")

    if selected_label:
        bill_data = df_filtered[df_filtered['Bill_Label'] == selected_label].copy()
        original_indices = bill_data.index
        display_df = bill_data.drop(columns=['Bill_Label', 'Date_Str'])
        
        edited_df = st.data_editor(display_df, key=f"edit_{suffix}", use_container_width=True)

        if st.button("💾 Save Bill Changes", key=f"save_{suffix}", type="primary"):
            final_df = purchases_df.drop(index=original_indices).copy()
            final_df = pd.concat([final_df, edited_df], ignore_index=True)
            save_purchases(final_df)
            st.cache_data.clear()
            st.success("✅ Bill updated successfully!")
            st.rerun()

with tab5:
    bill_editor(is_purchase=True, suffix="pur")
with tab6:
    bill_editor(is_purchase=False, suffix="sal")
