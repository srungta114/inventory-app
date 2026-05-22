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
        # Reads the new AI Memory Bank
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

# 2. Create the AI Memory dictionary
memory_dict = {}
if not learned_df.empty and 'Billed_Description' in learned_df.columns and 'Matched_Item_Name' in learned_df.columns:
    memory_dict = dict(zip(learned_df['Billed_Description'].astype(str).str.strip(), learned_df['Matched_Item_Name'].astype(str).str.strip()))

# Pre-process stock items for fuzzy matching
stock_items = products_df['Item_Name'].dropna().unique().tolist()
stock_items_lower = {str(item).lower(): item for item in stock_items}

def find_best_match(description):
    desc = str(description).strip()
    
    # --- NEW: Step 1. Check AI Memory Bank First ---
    if desc in memory_dict:
        return memory_dict[desc]
    
    # Step 2. Direct Substring Match
    desc_lower = desc.lower()
    for key, val in stock_items_lower.items():
        if desc_lower in key or key in desc_lower:
            return val
            
    # Step 3. Fuzzy Match Algorithm
    matches = get_close_matches(desc_lower, stock_items_lower.keys(), n=1, cutoff=0.5)
    if matches:
        return stock_items_lower[matches[0]]
        
    return None

st.title("📦 Hardware Inventory Management")
tab1, tab2, tab3, tab4 = st.tabs(["🛒 Record Purchase", "📊 View Inventory", "📋 Product Master", "📤 Bulk Upload Sales"])

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

# --- TAB 3: PRODUCT MASTER ---
with tab3:
    st.header("Base Product Master")
    st.dataframe(products_df, use_container_width=True, hide_index=True)

# --- TAB 4: BULK UPLOAD SALES ---
with tab4:
    st.header("Upload Sales Data (Deduct from Inventory)")
    uploaded_file = st.file_uploader("Upload Sales File (No Headers)", type=['csv', 'xlsx'])
    
    if uploaded_file is not None:
        if "processed_file_name" not in st.session_state or st.session_state.processed_file_name != uploaded_file.name:
            try:
                if uploaded_file.name.endswith('.csv'):
                    df_upload = pd.read_csv(uploaded_file, header=None)
                else:
                    df_upload = pd.read_excel(uploaded_file, header=None)
                
                auto_matched_records = []
                unmatched_raw_records = []
                
                for index, row in df_upload.iterrows():
                    date_val = row[0]
                    bill_val = row[1] 
                    qty_val = float(row[2])
                    
                    raw_item_code = str(row[4]).strip() if pd.notna(row[4]) else ""
                    other_desc = str(row[5]).strip() if pd.notna(row[5]) else ""
                    mapped_name = code_dict.get(raw_item_code, raw_item_code)
                    
                    if mapped_name and mapped_name.lower() != 'nan':
                        merged_description = f"{mapped_name} - {other_desc}"
                    else:
                        merged_description = other_desc
                    
                    # Fuzzy match now inherently uses the Memory Bank first!
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
                st.session_state.processed_file_name = uploaded_file.name
                
            except Exception as e:
                st.error(f"Error processing file: {e}")

        if "auto_matched" in st.session_state:
            auto_matched = st.session_state.auto_matched
            unmatched = st.session_state.unmatched
            
            if auto_matched:
                st.success(f"✅ Automatically matched {len(auto_matched)} items.")
                display_df = pd.DataFrame(auto_matched).drop(columns=['Display_Desc'], errors='ignore')
                with st.expander("View Auto-Matched Items"):
                    st.dataframe(display_df, use_container_width=True)
            
            final_records_to_commit = list(auto_matched) 
            
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
                        new_learned_rules = [] # --- NEW: List to hold what we just learned ---
                        
                        for un_row, selected_item in manual_selections:
                            if selected_item != "-- Skip / Do Not Import --":
                                item_details = products_df[products_df['Item_Name'] == selected_item].iloc[0]
                                
                                # 1. Prepare the inventory deduction record
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
                                
                                # 2. Document the new rule for the Memory Bank
                                new_learned_rules.append({
                                    "Billed_Description": un_row['Description'],
                                    "Matched_Item_Name": selected_item
                                })
                        
                        # Execute the updates
                        if final_records_to_commit:
                            # A. Save the Sales to Purchases tab
                            clean_records = [{k: v for k, v in r.items() if k != 'Display_Desc'} for r in final_records_to_commit]
                            new_records_df = pd.DataFrame(clean_records)
                            updated_purchases = pd.concat([purchases_df, new_records_df], ignore_index=True)
                            conn.update(worksheet="Purchases", data=updated_purchases)
                            
                            # B. Save the new knowledge to Learned_Mappings tab
                            if new_learned_rules:
                                new_rules_df = pd.DataFrame(new_learned_rules)
                                # Combine old rules with new rules, dropping duplicates so we only keep the newest manual override!
                                updated_learnings = pd.concat([learned_df, new_rules_df], ignore_index=True).drop_duplicates(subset=["Billed_Description"], keep="last")
                                conn.update(worksheet="Learned_Mappings", data=updated_learnings)
                            
                            # Reset app cache
                            st.cache_data.clear()
                            for key in ['auto_matched', 'unmatched', 'processed_file_name']: del st.session_state[key]
                            st.success("🎉 Database updated & AI Memory expanded successfully!")
                            st.rerun()
            else:
                if st.button("Commit Sales to Database", type="primary"):
                    if final_records_to_commit:
                        clean_records = [{k: v for k, v in r.items() if k != 'Display_Desc'} for r in final_records_to_commit]
                        new_records_df = pd.DataFrame(clean_records)
                        updated_purchases = pd.concat([purchases_df, new_records_df], ignore_index=True)
                        conn.update(worksheet="Purchases", data=updated_purchases)
                        st.cache_data.clear()
                        for key in ['auto_matched', 'unmatched', 'processed_file_name']: del st.session_state[key]
                        st.success("🎉 Database updated successfully!")
                        st.rerun()
