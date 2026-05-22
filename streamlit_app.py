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

products_df = get_product_master()
purchases_df = get_purchases()

# Pre-process stock items for fuzzy matching
stock_items = products_df['Item_Name'].dropna().unique().tolist()
stock_items_lower = {str(item).lower(): item for item in stock_items}

def find_best_match(description):
    desc = str(description).lower()
    for key, val in stock_items_lower.items():
        if desc in key or key in desc:
            return val
    matches = get_close_matches(desc, stock_items_lower.keys(), n=1, cutoff=0.5)
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
            
            # --- NEW: BILL NUMBER INPUT ---
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
                        "Bill Number": bill_number, # --- NEW: SAVING BILL NUMBER ---
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
                    bill_val = row[1] # --- NEW: EXTRACTING BILL NUMBER FROM FILE ---
                    qty_val = float(row[2])
                    description = row[5]
                    
                    matched_item = find_best_match(description)
                    
                    if matched_item:
                        item_details = products_df[products_df['Item_Name'] == matched_item].iloc[0]
                        auto_matched_records.append({
                            "Date": date_val,
                            "Bill Number": bill_val, # --- NEW: SAVING BILL NUMBER ---
                            "Group": item_details['Group'], 
                            "Item_Name": matched_item,
                            "Purchase Qty": 0, 
                            "Purchase Unit": "-", 
                            "Stock Qty Added": -abs(qty_val), 
                            "Stock Unit": item_details['Sales_Unit'], 
                            "Display_Desc": description
                        })
                    else:
                        unmatched_raw_records.append({
                            "Date": date_val, 
                            "Bill Number": bill_val, # --- NEW: SAVING BILL NUMBER ---
                            "Qty": qty_val, 
                            "Description": description
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
                        for un_row, selected_item in manual_selections:
                            if selected_item != "-- Skip / Do Not Import --":
                                item_details = products_df[products_df['Item_Name'] == selected_item].iloc[0]
                                final_records_to_commit.append({
                                    "Date": un_row['Date'], 
                                    "Bill Number": un_row['Bill Number'], # --- NEW: SAVING BILL NUMBER ---
                                    "Group": item_details['Group'], 
                                    "Item_Name": selected_item,
                                    "Purchase Qty": 0, 
                                    "Purchase Unit": "-", 
                                    "Stock Qty Added": -abs(un_row['Qty']), 
                                    "Stock Unit": item_details['Sales_Unit'], 
                                    "Display_Desc": un_row['Description']
                                })
                        
                        if final_records_to_commit:
                            clean_records = [{k: v for k, v in r.items() if k != 'Display_Desc'} for r in final_records_to_commit]
                            new_records_df = pd.DataFrame(clean_records)
                            updated_purchases = pd.concat([purchases_df, new_records_df], ignore_index=True)
                            conn.update(worksheet="Purchases", data=updated_purchases)
                            st.cache_data.clear()
                            for key in ['auto_matched', 'unmatched', 'processed_file_name']: del st.session_state[key]
                            st.success("🎉 Database updated successfully!")
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
