import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime
from difflib import get_close_matches

st.set_page_config(page_title="Hardware Inventory", layout="wide", page_icon="📦")

# --- CONNECT TO GOOGLE SHEETS ---
conn = st.connection("gsheets", type=GSheetsConnection)

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
        return pd.DataFrame(columns=["Item Code", "Item Name"])

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

# 1. Create the Product Code translation dictionary
code_dict = {}
if not mapping_df.empty and 'Item Code' in mapping_df.columns and 'Item Name' in mapping_df.columns:
    code_dict = dict(zip(mapping_df['Item Code'].astype(str).str.strip(), mapping_df['Item Name'].astype(str).str.strip()))

# 2. Create the AI Memory dictionary (Standardized to UPPERCASE to prevent dupes)
memory_dict = {}
if not learned_df.empty and 'Billed_Description' in learned_df.columns and 'Matched_Item_Name' in learned_df.columns:
    memory_dict = dict(zip(learned_df['Billed_Description'].astype(str).str.strip().str.upper(), learned_df['Matched_Item_Name'].astype(str).str.strip()))

# Pre-process stock items for fuzzy matching
stock_items = products_df['Item_Name'].dropna().unique().tolist()
stock_items_lower = {str(item).lower(): item for item in stock_items}

def find_best_match(description):
    desc_clean_upper = str(description).strip().upper()
    
    # Step 1. Check AI Memory Bank First
    if desc_clean_upper in memory_dict:
        return memory_dict[desc_clean_upper]
    
    # Step 2. Direct Substring Match
    desc_lower = str(description).strip().lower()
    for key, val in stock_items_lower.items():
        if desc_lower in key or key in desc_lower:
            return val
            
    # Step 3. Fuzzy Match Algorithm
    matches = get_close_matches(desc_lower, stock_items_lower.keys(), n=1, cutoff=0.5)
    if matches:
        return stock_items_lower[matches[0]]
        
    return None

st.title("📦 Hardware Inventory Management")
tab1, tab2, tab3, tab4 = st.tabs(["🛒 Record Purchase", "📊 View Inventory", "📋 Masters & AI Memory", "📤 Bulk Upload Sales"])

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
                        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Bill Number": bill_number, 
                        "Group": group,
                        "Item_Name": selected_item,
                        "Purchase Qty": purchase_qty,
                        "Purchase Unit": p_unit,
                        "Stock Qty Added": final_stock, 
                        "Stock Unit": s_unit
                    }])
                    updated_purchases = pd.concat([purchases_df, new_record], ignore_index=True)
                    conn.update(worksheet="Purchases", data=updated_purchases)
                    st.cache_data.clear()
                    st.success(f"✅ Saved! Added {final_stock} {s_unit} to inventory under Bill: {bill_number}")

# --- TAB 2: VIEW INVENTORY ---
with tab2:
    st.header("Live Stock Levels")
    if not purchases_df.empty:
        inventory_summary = purchases_df.groupby(['Group', 'Item_Name', 'Stock Unit'])['Stock Qty Added'].sum().reset_index()
        inventory_summary.rename(columns={'Stock Qty Added': 'Total Stock on Hand'}, inplace=True)
        search_term = st.text_input("🔍 Search for an item...", value="", placeholder="Type here to search...")
        if search_term:
            inventory_summary = inventory_summary[inventory_summary['Item_Name'].str.contains(search_term, case=False)]
        st.dataframe(inventory_summary, use_container_width=True, hide_index=True)
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
        
        # --- NEW: Check if this exact file was ALREADY successfully saved to prevent infinite loops ---
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

            # 3. FUZZY MATCHING (Runs after duplicates are resolved)
            if not st.session_state.get("resolving_duplicates", False) and "auto_matched" not in st.session_state and "df_to_process" in st.session_state:
                df_to_process = st.session_state.df_to_process
                auto_matched_records = []
                unmatched_raw_records = []
                
                for index, row in df_to_process.iterrows():
                    date_val = row[0]
                    bill_val = str(row[1]).strip()
                    qty_val = float(row[2])
                    
                    raw_item_code = str(row[4]).strip() if pd.notna(row[4]) else ""
                    other_desc = str(row[5]).strip() if pd.notna(row[5]) else ""
                    mapped_name = code_dict.get(raw_item_code, raw_item_code)
                    
                    if mapped_name and mapped_name.lower() != 'nan':
                        merged_description = f"{mapped_name} - {other_desc}"
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
                            "Display_Desc": merged_description
                        })
                    else:
                        unmatched_raw_records.append({
                            "Date": date_val, 
                            "Bill Number": bill_val, 
                            "Qty": qty_val, 
                            "Description": merged_description
                        })
                
                st.session_state.auto_matched = auto_matched_records
                st.session_state.unmatched = unmatched_raw_records
                st.rerun()

            # 4. FINAL REVIEW & COMMIT UI
            if "auto_matched" in st.session_state:
                auto_matched = st.session_state.auto_matched
                unmatched = st.session_state.unmatched
                
                if auto_matched:
                    st.success(f"✅ Automatically matched {len(auto_matched)} items.")
                    display_df = pd.DataFrame(auto_matched).drop(columns=['Display_Desc'], errors='ignore')
                    with st.expander("View Auto-Matched Items"):
                        st.dataframe(display_df, use_container_width=True)
                
                final_records_to_commit = list(auto_matched) 
                
                # --- Unified Safe Commit Function ---
                def commit_sales_to_db(new_learned=None):
                    clean_records = [{k: v for k, v in r.items() if k != 'Display_Desc'} for r in final_records_to_commit]
                    new_records_df = pd.DataFrame(clean_records)
                    
                    current_purchases = purchases_df.copy()
                    bills_to_delete = st.session_state.get("bills_to_delete", [])
                    
                    # Apply Overrides (Remove old bills marked for deletion)
                    if bills_to_delete and not current_purchases.empty and 'Bill Number' in current_purchases.columns:
                        current_purchases = current_purchases[~current_purchases['Bill Number'].astype(str).str.strip().isin(bills_to_delete)]
                    
                    # Save Sales (if there are new records, OR if we deleted overrides)
                    if not new_records_df.empty or bills_to_delete:
                        if not new_records_df.empty:
                            updated_purchases = pd.concat([current_purchases, new_records_df], ignore_index=True)
                        else:
                            updated_purchases = current_purchases
                        conn.update(worksheet="Purchases", data=updated_purchases)
                    
                    # Save the AI Memory Bank
                    if new_learned:
                        new_rules_df = pd.DataFrame(new_learned)
                        updated_learnings = pd.concat([learned_df, new_rules_df], ignore_index=True)
                        updated_learnings['Billed_Description'] = updated_learnings['Billed_Description'].astype(str).str.strip().str.upper()
                        updated_learnings = updated_learnings.drop_duplicates(subset=["Billed_Description"], keep="last")
                        conn.update(worksheet="Learned_Mappings", data=updated_learnings)
                    
                    # Clean the cache
                    st.cache_data.clear()
                    
                    # --- NEW: Lock the file from looping ---
                    st.session_state.committed_file_name = st.session_state.processed_file_name
                    
                    # Wipe temporary states
                    keys_to_clear = ['auto_matched', 'unmatched', 'raw_upload_data', 'resolving_duplicates', 'df_to_process', 'bills_to_delete']
                    for key in keys_to_clear:
                        if key in st.session_state:
                            del st.session_state[key]
                            
                    st.rerun()

                if unmatched:
                    st.warning(f"⚠️ {len(unmatched)} items could not be matched automatically.")
                    with st.form("manual_mapping_form"):
                        manual_selections = []
                        h1, h2, h3, h4 = st.columns([1, 2, 1, 3])
                        h1.write("**Bill No**")
                        h2.write("**Billed Description**")
                        h3.write("**Qty**")
                        h4.write("**Match to Master Product**")
                        st.divider()
                        
                        for idx, un_row in enumerate(unmatched):
                            c1, c2, c3, c4 = st.columns([1, 2, 1, 3])
                            with c1: st.write(un_row['Bill Number'])
                            with c2: st.write(un_row['Description'])
                            with c3: st.write(un_row['Qty'])
                            with c4:
                                selected = st.selectbox("Match", options=["-- Skip / Do Not Import --"] + stock_items, key=f"un_{idx}", label_visibility="collapsed")
                            manual_selections.append((un_row, selected))
                            
                        st.write("")
                        if st.form_submit_button("Confirm Manual Matches & Commit ALL Sales", type="primary"):
                            new_learned_rules = [] 
                            
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
                                        "Stock Unit": item_details['Sales_Unit'], 
                                        "Display_Desc": un_row['Description']
                                    })
                                    
                                    new_learned_rules.append({
                                        "Billed_Description": str(un_row['Description']).strip().upper(),
                                        "Matched_Item_Name": selected_item
                                    })
                            
                            if final_records_to_commit or st.session_state.get("bills_to_delete"):
                                commit_sales_to_db(new_learned_rules)
                else:
                    if st.button("Commit Sales to Database", type="primary"):
                        if final_records_to_commit or st.session_state.get("bills_to_delete"):
                            commit_sales_to_db()

    else:
        # --- NEW: Clean up memory lock when you remove the file ---
        keys_to_clear = ['auto_matched', 'unmatched', 'processed_file_name', 'raw_upload_data', 'resolving_duplicates', 'df_to_process', 'bills_to_delete', 'committed_file_name']
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]
