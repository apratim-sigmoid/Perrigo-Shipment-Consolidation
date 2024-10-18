import streamlit as st
import pandas as pd
import numpy as np
import time
import datetime
from itertools import product
from collections import Counter, defaultdict
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
from PIL import Image
from pyecharts import options as opts
from pyecharts.charts import Calendar, Page
from pyecharts.globals import ThemeType
from pyecharts.commons.utils import JsCode
import streamlit.components.v1 as components
from bokeh.plotting import figure, show
from bokeh.models import ColumnDataSource, HoverTool, ColorBar
from bokeh.palettes import RdYlGn11
from bokeh.transform import linear_cmap



# Set page config
st.set_page_config(page_title="Shipment Optimization & Planning Dashboard", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] {
        padding-top: 0rem;

    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 0rem;
        margin-top: -1rem;
    }
    [data-testid="stSidebarNav"] {
        padding-top: 0rem;
    }
</style>
""", unsafe_allow_html=True)


logo = Image.open("perrigo-logo.png")
st.image(logo, width=120)

# Title
st.title("Shipment Optimization & Planning Dashboard")

# Load data and rate cards
@st.cache_data
def load_data():
    df = pd.read_excel('Complete Input.xlsx', sheet_name='Sheet1')
    df['SHIPPED_DATE'] = pd.to_datetime(df['SHIPPED_DATE'], dayfirst=True)
    rate_card_ambient = pd.read_excel('Complete Input.xlsx', sheet_name='AMBIENT')
    rate_card_ambcontrol = pd.read_excel('Complete Input.xlsx', sheet_name='AMBCONTROL')
    return df, rate_card_ambient, rate_card_ambcontrol

df, rate_card_ambient, rate_card_ambcontrol = load_data()

# Sidebar
st.sidebar.header("Parameters")

# Add slider for total shipment capacity
total_shipment_capacity = st.sidebar.slider("Total Shipment Capacity", 26, 52, 46)

# Group selection
group_method = st.sidebar.radio("Consolidation Level", ('Post Code Level', 'Customer Level'))
group_field = 'SHORT_POSTCODE' if group_method == 'Post Code Level' else 'NAME'


# Month selection
all_months = ['Custom', 'All', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
selected_month = st.sidebar.selectbox(
    "Select Month for Consolidation",
    options=all_months,
    index=2  # Default to 'Jan'
)

# Add date range selector for Custom option
if selected_month == 'Custom':
    
    # Create date objects for the first and last day of 2023
    min_date = datetime.date(2023, 1, 1)
    max_date = datetime.date(2023, 12, 31)
    
    # Use date_input with min and max values set to 2023
    start_date = st.sidebar.date_input("Start date", value=min_date, min_value=min_date, max_value=max_date)
    end_date = st.sidebar.date_input("End date", value=max_date, min_value=min_date, max_value=max_date)
    
    # Convert date to datetime at midnight for compatibility with DataFrame
    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
elif selected_month == 'All':
    start_date = df['SHIPPED_DATE'].min()
    end_date = df['SHIPPED_DATE'].max()
else:
    # Convert month name to number
    month_to_num = {month: index for index, month in enumerate(all_months[2:], start=1)}
    selected_month_number = month_to_num[selected_month]
    
    # Set year to 2023
    year = 2023
    
    # Set start_date to the first day of the selected month
    start_date = pd.Timestamp(year=year, month=selected_month_number, day=1)
    
    # Set end_date to the last day of the selected month
    if selected_month_number == 12:
        end_date = pd.Timestamp(year=year, month=12, day=31, hour=23, minute=59, second=59)
    else:
        end_date = pd.Timestamp(year=year, month=selected_month_number + 1, day=1) - pd.Timedelta(seconds=1)

# Filter data based on selected date range
df = df[(df['SHIPPED_DATE'] >= start_date) & (df['SHIPPED_DATE'] <= end_date)]


# Add checkbox and conditional dropdown for selecting post codes or customers
if group_method == 'Post Code Level':
    all_postcodes = st.sidebar.checkbox("All Post Codes", value=False)
    
    if not all_postcodes:
        postcode_counts = df['SHORT_POSTCODE'].value_counts()
        postcode_options = postcode_counts.index.tolist()
        selected_postcodes = st.sidebar.multiselect(
            "Select Post Codes",
            options=postcode_options,
            default=[postcode_options[0]],
            format_func=lambda x: f"{x} ({postcode_counts[x]})"
        )
else:  # Customer Level
    all_customers = st.sidebar.checkbox("All Customers", value=False)
    
    if not all_customers:
        customer_counts = df['NAME'].value_counts()
        customer_options = customer_counts.index.tolist()
        selected_customers = st.sidebar.multiselect(
            "Select Customers",
            options=customer_options,
            default=[customer_options[0]],
            format_func=lambda x: f"{x} ({customer_counts[x]})"
        )

# Filter the dataframe based on the selection
if group_method == 'Post Code Level' and not all_postcodes:
    if selected_postcodes:  # Only filter if some postcodes are selected
        df = df[df['SHORT_POSTCODE'].isin(selected_postcodes)]
    
elif group_method == 'Customer Level' and not all_customers:
    if selected_customers:  # Only filter if some customers are selected
        df = df[df['NAME'].isin(selected_customers)]
      
    
# Create tabs
tab1, tab2 = st.tabs(["Simulation", "Calculation"])


# Helper functions
def calculate_priority(shipped_date, current_date, shipment_window):
    days_left = (shipped_date - current_date).days
    if 0 <= days_left <= shipment_window:
        return days_left
    return np.nan

def get_shipment_cost(prod_type, short_postcode, total_pallets):
    if prod_type == 'AMBIENT':
        rate_card = rate_card_ambient
    elif prod_type == 'AMBCONTROL':
        rate_card = rate_card_ambcontrol
    else:
        return np.nan

    row = rate_card[rate_card['SHORT_POSTCODE'] == short_postcode]
    
    if row.empty:
        return np.nan

    cost_per_pallet = row.get(total_pallets, np.nan).values[0]

    if pd.isna(cost_per_pallet):
        return np.nan

    shipment_cost = round(cost_per_pallet * total_pallets, 1)
    return shipment_cost

def get_baseline_cost(prod_type, short_postcode, pallets):
    total_cost = 0
    for pallet in pallets:
        cost = get_shipment_cost(prod_type, short_postcode, pallet)
        if pd.isna(cost):
            return np.nan
        total_cost += cost
    return round(total_cost, 1)

def best_fit_decreasing(items, capacity):
    items = sorted(items, key=lambda x: x['Total Pallets'], reverse=True)
    shipments = []

    for item in items:
        best_shipment = None
        min_space = capacity + 1

        for shipment in shipments:
            current_load = sum(order['Total Pallets'] for order in shipment)
            new_load = current_load + item['Total Pallets']
            
            if new_load <= capacity:
                space_left = capacity - current_load
            else:
                continue  # Skip this shipment if adding the item would exceed capacity
            
            if item['Total Pallets'] <= space_left < min_space:
                best_shipment = shipment
                min_space = space_left

        if best_shipment:
            best_shipment.append(item)
        else:
            shipments.append([item])

    return shipments

def process_shipment(shipment, consolidated_shipments, allocation_matrix, working_df, current_date, capacity):
    total_pallets = sum(order['Total Pallets'] for order in shipment)
    utilization = (total_pallets / capacity) * 100

    prod_type = shipment[0]['PROD TYPE']
    short_postcode = shipment[0]['SHORT_POSTCODE']
    shipment_cost = get_shipment_cost(prod_type, short_postcode, total_pallets)

    pallets = [order['Total Pallets'] for order in shipment]
    baseline_cost = get_baseline_cost(prod_type, short_postcode, pallets)

    shipment_info = {
        'Date': current_date,
        'Orders': [order['ORDER_ID'] for order in shipment],
        'Total Pallets': total_pallets,
        'Capacity': capacity,
        'Utilization %': round(utilization, 1),
        'Order Count': len(shipment),
        'Pallets': pallets,
        'PROD TYPE': prod_type,
        'GROUP': shipment[0]['GROUP'],
        'Shipment Cost': shipment_cost,
        'Baseline Cost': baseline_cost,
        'SHORT_POSTCODE': short_postcode,
        'Load Type': 'Full' if total_pallets > 26 else 'Partial'
    }

    if group_method == 'NAME':
        shipment_info['NAME'] = shipment[0]['NAME']

    consolidated_shipments.append(shipment_info)
    
    for order in shipment:
        allocation_matrix.loc[order['ORDER_ID'], current_date] = 1
        working_df.drop(working_df[working_df['ORDER_ID'] == order['ORDER_ID']].index, inplace=True)

def consolidate_shipments(df, high_priority_limit, utilization_threshold, shipment_window, date_range, progress_callback, capacity):
    consolidated_shipments = []
    allocation_matrix = pd.DataFrame(0, index=df['ORDER_ID'], columns=date_range)
    
    working_df = df.copy()
    
    for current_date in date_range:
        working_df.loc[:, 'Priority'] = working_df['SHIPPED_DATE'].apply(lambda x: calculate_priority(x, current_date, shipment_window))

        if (working_df['Priority'] == 0).any():
            eligible_orders = working_df[working_df['Priority'].notnull()].sort_values('Priority')
            high_priority_orders = eligible_orders[eligible_orders['Priority'] <= high_priority_limit].to_dict('records')
            low_priority_orders = eligible_orders[eligible_orders['Priority'] > high_priority_limit].to_dict('records')
            
            if high_priority_orders or low_priority_orders:
                # Process high priority orders first
                high_priority_shipments = best_fit_decreasing(high_priority_orders, capacity)
                
                # Try to fill high priority shipments with low priority orders
                for shipment in high_priority_shipments:
                    current_load = sum(order['Total Pallets'] for order in shipment)
                    space_left = capacity - current_load  # Use the variable capacity
                    
                    if space_left > 0:
                        low_priority_orders.sort(key=lambda x: x['Total Pallets'], reverse=True)
                        for low_priority_order in low_priority_orders[:]:
                            if low_priority_order['Total Pallets'] <= space_left:
                                shipment.append(low_priority_order)
                                space_left -= low_priority_order['Total Pallets']
                                low_priority_orders.remove(low_priority_order)
                            if space_left == 0:
                                break
                
                # Process remaining low priority orders
                low_priority_shipments = best_fit_decreasing(low_priority_orders, capacity)
                
                # Process all shipments
                all_shipments = high_priority_shipments + low_priority_shipments
                for shipment in all_shipments:
                    total_pallets = sum(order['Total Pallets'] for order in shipment)
                    utilization = (total_pallets / capacity) * 100
                    
                    # Always process shipments with high priority orders, apply threshold only to pure low priority shipments
                    if any(order['Priority'] <= high_priority_limit for order in shipment) or utilization >= utilization_threshold:
                        process_shipment(shipment, consolidated_shipments, allocation_matrix, working_df, current_date, capacity)
        
        progress_callback()
    
    return consolidated_shipments, allocation_matrix

def calculate_metrics(all_consolidated_shipments, df):
    total_orders = sum(len(shipment['Orders']) for shipment in all_consolidated_shipments)
    total_shipments = len(all_consolidated_shipments)
    total_pallets = sum(shipment['Total Pallets'] for shipment in all_consolidated_shipments)
    total_utilization = sum(shipment['Utilization %'] for shipment in all_consolidated_shipments)
    average_utilization = total_utilization / total_shipments if total_shipments > 0 else 0
    total_shipment_cost = sum(shipment['Shipment Cost'] for shipment in all_consolidated_shipments if not pd.isna(shipment['Shipment Cost']))
    total_baseline_cost = sum(shipment['Baseline Cost'] for shipment in all_consolidated_shipments if not pd.isna(shipment['Baseline Cost']))
    cost_savings = total_baseline_cost - total_shipment_cost
    percent_savings = (cost_savings / total_baseline_cost) * 100 if total_baseline_cost > 0 else 0

    # Calculate CO2 Emission
    total_distance = 0
    sum_dist = 0
    for shipment in all_consolidated_shipments:
        order_ids = shipment['Orders']
        avg_distance = df[df['ORDER_ID'].isin(order_ids)]['Distance'].mean()
        sum_distance = df[df['ORDER_ID'].isin(order_ids)]['Distance'].sum()
        total_distance += avg_distance
        sum_dist += sum_distance
    co2_emission = (sum_dist - total_distance) * 2  # Multiply by 2 


    metrics = {
        'Total Orders': total_orders,
        'Total Shipments': total_shipments,
        'Total Pallets': total_pallets,
        'Average Utilization': average_utilization,
        'Total Shipment Cost': total_shipment_cost,
        'Total Baseline Cost': total_baseline_cost,
        'Cost Savings': round(cost_savings,1),
        'Percent Savings': percent_savings,
        'CO2 Emission': co2_emission
    }

    return metrics

def create_utilization_chart(all_consolidated_shipments):
    utilization_bins = {f"{i}-{i+5}%": 0 for i in range(0, 100, 5)}
    for shipment in all_consolidated_shipments:
        utilization = shipment['Utilization %']
        bin_index = min(int(utilization // 5) * 5, 95)  # Cap at 95-100% bin
        bin_key = f"{bin_index}-{bin_index+5}%"
        utilization_bins[bin_key] += 1

    total_shipments = len(all_consolidated_shipments)
    utilization_distribution = {bin: (count / total_shipments) * 100 for bin, count in utilization_bins.items()}

    fig = go.Figure(data=[go.Bar(x=list(utilization_distribution.keys()), y=list(utilization_distribution.values()), marker_color='#1f77b4')])
    fig.update_layout(
        title={'text': 'Utilization Distribution', 'font': {'size': 22}},
        xaxis_title='Utilization Range',
        yaxis_title='Percentage of Shipments', 
        width=1000, 
        height=500
        )

    return fig

def create_pallet_distribution_chart(all_consolidated_shipments, total_shipment_capacity):
    # Create bins with 5-pallet intervals
    bin_size = 5
    num_bins = (total_shipment_capacity + bin_size - 1) // bin_size  # Round up to nearest bin
    pallet_bins = {f"{i*bin_size+1}-{min((i+1)*bin_size, total_shipment_capacity)}": 0 for i in range(num_bins)}

    for shipment in all_consolidated_shipments:
        total_pallets = shipment['Total Pallets']
        for bin_range, count in pallet_bins.items():
            low, high = map(int, bin_range.split('-'))
            if low <= total_pallets <= high:
                pallet_bins[bin_range] += 1
                break

    total_shipments = len(all_consolidated_shipments)
    pallet_distribution = {bin: (count / total_shipments) * 100 for bin, count in pallet_bins.items()}

    # Sort the bins to ensure they're in the correct order
    sorted_bins = sorted(pallet_distribution.items(), key=lambda x: int(x[0].split('-')[0]))

    fig = go.Figure(data=[go.Bar(x=[bin for bin, _ in sorted_bins], 
                                 y=[value for _, value in sorted_bins],
                                 marker_color='#1f77b4')])
    fig.update_layout(
        title={'text': 'Pallet Distribution', 'font': {'size': 22, 'weight': 'normal'}},
        xaxis_title='Pallet Range',
        yaxis_title='Percentage of Shipments',
        xaxis=dict(tickangle=0),
        width=600,
        height=500
    )
    return fig


def create_consolidated_shipments_calendar(consolidated_df):
    # Group by Date and calculate both Shipments Count and Total Orders
    df_consolidated = consolidated_df.groupby('Date').agg({
        'Orders': ['count', lambda x: sum(len(orders) for orders in x)]  # Count rows for shipments, sum order lengths for total orders
    }).reset_index()
    df_consolidated.columns = ['Date', 'Shipments Count', 'Orders Count']
    
    calendar_data_consolidated = df_consolidated[['Date', 'Shipments Count', 'Orders Count']].values.tolist()

    calendar = (
        Calendar(init_opts=opts.InitOpts(width="1300px", height="220px", theme=ThemeType.ROMANTIC))
        .add(
            series_name="",
            yaxis_data=calendar_data_consolidated,
            calendar_opts=opts.CalendarOpts(
                pos_top="50",
                pos_left="40",
                pos_right="30",
                range_=str(calendar_data_consolidated[0][0].year),
                yearlabel_opts=opts.CalendarYearLabelOpts(is_show=False),
                daylabel_opts=opts.CalendarDayLabelOpts(name_map=['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']),
                monthlabel_opts=opts.CalendarMonthLabelOpts(name_map="en"),
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="Calendar Heatmap for Orders and Shipments Consolidated"),
            visualmap_opts=opts.VisualMapOpts(
                max_=max(item[2] for item in calendar_data_consolidated),  # Changed from item[1] to item[2]
                min_=min(item[2] for item in calendar_data_consolidated),  # Changed from item[1] to item[2]
                orient="horizontal",
                is_piecewise=False,
                pos_bottom="20",
                pos_left="center",
                range_color=["#E8F5E9", "#1B5E20"],
                is_show=False,
            ),
            tooltip_opts=opts.TooltipOpts(
                formatter=JsCode(
                    """
                    function (p) {
                        var date = new Date(p.data[0]);
                        var day = date.getDate().toString().padStart(2, '0');
                        var month = (date.getMonth() + 1).toString().padStart(2, '0');
                        var year = date.getFullYear();
                        return 'Date: ' + day + '/' + month + '/' + year + 
                               '<br/>Orders: ' + p.data[2] +
                               '<br/>Shipments: ' + p.data[1];
                    }
                    """
                )
            )
        )
    )
    return calendar

def create_original_orders_calendar(original_df):
    df_original = original_df.groupby('SHIPPED_DATE').size().reset_index(name='Orders Shipped')
    calendar_data_original = df_original[['SHIPPED_DATE', 'Orders Shipped']].values.tolist()

    calendar = (
        Calendar(init_opts=opts.InitOpts(width="1300px", height="220px", theme=ThemeType.ROMANTIC))
        .add(
            series_name="",
            yaxis_data=calendar_data_original,
            calendar_opts=opts.CalendarOpts(
                pos_top="50",
                pos_left="40",
                pos_right="30",
                range_=str(calendar_data_original[0][0].year),
                yearlabel_opts=opts.CalendarYearLabelOpts(is_show=False),
                daylabel_opts=opts.CalendarDayLabelOpts(name_map=['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']),
                monthlabel_opts=opts.CalendarMonthLabelOpts(name_map="en"),
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="Calendar Heatmap for Orders Shipped Before Consolidation"),
            visualmap_opts=opts.VisualMapOpts(
                max_=max(item[1] for item in calendar_data_original),
                min_=min(item[1] for item in calendar_data_original),
                orient="horizontal",
                is_piecewise=False,
                pos_bottom="20",
                pos_left="center",
                range_color=["#E8F5E9", "#1B5E20"],
                is_show=False,
            ),
            tooltip_opts=opts.TooltipOpts(
                formatter=JsCode(
                    """
                    function (p) {
                        var date = new Date(p.data[0]);
                        var day = date.getDate().toString().padStart(2, '0');
                        var month = (date.getMonth() + 1).toString().padStart(2, '0');
                        var year = date.getFullYear();
                        return 'Date: ' + day + '/' + month + '/' + year + '<br/>Orders: ' + p.data[1];
                    }
                    """
                )
            )
        )
    )
    return calendar

def create_heatmap_charts(consolidated_df, original_df):
    chart_consolidated = create_consolidated_shipments_calendar(consolidated_df)
    chart_original = create_original_orders_calendar(original_df)

    page = Page(layout=Page.SimplePageLayout)
    page.add(chart_consolidated, chart_original)

    return page


def analyze_consolidation_distribution(all_consolidated_shipments, df):
    distribution = {}
    for shipment in all_consolidated_shipments:
        consolidation_date = shipment['Date']
        for order_id in shipment['Orders']:
            shipped_date = df.loc[df['ORDER_ID'] == order_id, 'SHIPPED_DATE'].iloc[0]
            days_difference = (shipped_date - consolidation_date).days
            if days_difference not in distribution:
                distribution[days_difference] = 0
            distribution[days_difference] += 1
    
    total_orders = sum(distribution.values())
    distribution_percentage = {k: round((v / total_orders) * 100, 1) for k, v in distribution.items()}
    return distribution, distribution_percentage


def custom_progress_bar():
    progress_container = st.empty()
    
    def render_progress(percent):
        progress_html = f"""
        <style>
            .overall-container {{
                width: 100%;
                position: relative;
                padding-top: 30px; /* Reduced space for the truck */
            }}
            .progress-container {{
                width: 100%;
                height: 8px;
                background-color: #bbddff;
                border-radius: 10px;
                position: relative;
                overflow: hidden;
            }}
            .progress-bar {{
                width: {percent}%;
                height: 100%;
                background-color: #0053a4;
                border-radius: 10px;
                transition: width 0.5s ease-in-out;
            }}
            .truck-icon {{
                position: absolute;
                top: 0;
                left: calc({percent}% - 15px);
                transition: left 0.5s ease-in-out;
            }}
        </style>
        <div class="overall-container">
            <div class="truck-icon">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 512" width="30" height="30">
                    <path fill="#0053a4" d="M624 352h-16V243.9c0-12.7-5.1-24.9-14.1-33.9L494 110.1c-9-9-21.2-14.1-33.9-14.1H416V48c0-26.5-21.5-48-48-48H48C21.5 0 0 21.5 0 48v320c0 26.5 21.5 48 48 48h16c0 53 43 96 96 96s96-43 96-96h128c0 53 43 96 96 96s96-43 96-96h48c8.8 0 16-7.2 16-16v-32c0-8.8-7.2-16-16-16zm-464 96c-26.5 0-48-21.5-48-48s21.5-48 48-48 48 21.5 48 48-21.5 48-48 48zm208-96H242.7c-16.6-28.6-47.2-48-82.7-48s-66.1 19.4-82.7 48H48V48h352v304zm96 96c-26.5 0-48-21.5-48-48s21.5-48 48-48 48 21.5 48 48-21.5 48-48 48zm80-96h-16.7c-16.6-28.6-47.2-48-82.7-48-29.2 0-55.1 14.2-71.3 36-3.1-1.9-6.4-3.5-9.9-4.7V144h80.9c4.7 0 9.2 1.9 12.5 5.2l100.2 100.2c2.1 2.1 3.3 5 3.3 8v95.8z"/>
                </svg>
            </div>
            <div class="progress-container">
                <div class="progress-bar"></div>
            </div>
        </div>
        """
        progress_container.markdown(progress_html, unsafe_allow_html=True)
    
    return render_progress


def create_metric_box(label, value, color_start="#1f77b4", color_end="#0053a4"):
    html = f"""
    <div style="
        background: linear-gradient(135deg, {color_start} 0%, {color_end} 100%);
        padding: 5px;
        border-radius: 15px;
        margin: 0px 0 20px 0;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.4);
        border: 2px solid #ffffff;
        position: relative;
        overflow: hidden;
    ">
        <h4 style="color: white; margin: 0; font-size: 16px; font-weight: 500; position: relative; z-index: 1;">{label}</h4>
        <p style="color: white; font-size: 18px; font-weight: 600; margin: 0px 0 0 0; position: relative; z-index: 1;">{value}</p>
        <div style="
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: transparent;
            pointer-events: none;
        "></div>
    </div>
    """
    return st.markdown(html, unsafe_allow_html=True)

    
# Simulation tab
with tab1:
    st.markdown("<h2 style='font-size:24px;'>Simulation Parameters</h2>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)

    with col1:
        shipment_window_range = st.slider("Shipment Window", 0, 30, (2, 7))

    with col2:
        utilization_threshold_range = st.slider("Utilization Threshold", 25, 95, (60, 80), step=5)

    with col3:
        with st.expander("Iteration Breakdown", expanded=False):
            # Calculate the values dynamically
            total_days = (end_date - start_date).days + 1
            grouped = df.groupby(['PROD TYPE', group_field])
            total_groups = len(grouped)
            utilization_thresholds = range(utilization_threshold_range[0], utilization_threshold_range[1] + 1, 5)
            shipment_windows = range(shipment_window_range[0], shipment_window_range[1] + 1)
        
            # Display the breakdown
            st.write(f"Total days to check consolidation: {total_days}")
            st.write(f"({start_date.date()} to {end_date.date()})")
            st.write(f"Total groups (PROD TYPE, {group_field}): {total_groups}")
            st.write(f"Utilization thresholds: {', '.join(map(str, utilization_thresholds))}")
            st.write(f"Shipment windows: {', '.join(map(str, shipment_windows))}")
            
            # Calculate total iterations
            total_iterations = len(shipment_windows) * len(utilization_thresholds) * total_groups * total_days
            total_calculations = len(shipment_windows) * len(utilization_thresholds)
            st.write(f"\nTotal iterations: {total_iterations:,} (Total Calculations: {total_calculations:,})")    
        

    if st.button("Run Simulation"):
        start_time = time.time()
        
        with st.spinner("Running simulation..."):
            # Prepare data for simulation
            df['GROUP'] = df[group_field]
            grouped = df.groupby(['PROD TYPE', 'GROUP'])
            date_range = pd.date_range(start=start_date, end=end_date)
            
            # Generate all combinations of parameters
            shipment_windows = range(shipment_window_range[0], shipment_window_range[1] + 1)
            utilization_thresholds = range(utilization_threshold_range[0], utilization_threshold_range[1] + 1, 5)
            
            total_groups = len(grouped)
            total_days = len(date_range)
            total_iterations = len(shipment_windows) * len(utilization_thresholds) * total_groups * total_days
    
            # Initialize variables to store best results
            best_metrics = None
            best_consolidated_shipments = None
            best_params = None
            
            # Create a progress bar
            progress_bar = custom_progress_bar()
            iteration_counter = 0
            
            all_results = []
            
            # Run simulation for each combination
            for shipment_window in shipment_windows:
                for utilization_threshold in utilization_thresholds:
                    high_priority_limit =0
                    all_consolidated_shipments = []
                
                    for (prod_type, group), group_df in grouped:
                        consolidated_shipments, _ = consolidate_shipments(
                            group_df, high_priority_limit, utilization_threshold, 
                            shipment_window, date_range, lambda: None, total_shipment_capacity
                        )
                        all_consolidated_shipments.extend(consolidated_shipments)
                        
                        iteration_counter += total_days
                        progress_percentage = int((iteration_counter / total_iterations) * 100)
                        progress_bar(progress_percentage)
                
                    # Calculate metrics for this combination
                    metrics = calculate_metrics(all_consolidated_shipments, df)
                    
                    # Analyze consolidation distribution
                    distribution, distribution_percentage = analyze_consolidation_distribution(all_consolidated_shipments, df)
                    
                    result = {
                        'Shipment Window': shipment_window,
                        'Utilization Threshold': utilization_threshold,
                        'Total Orders': metrics['Total Orders'],
                        'Total Shipments': metrics['Total Shipments'],
                        'Total Shipment Cost': round(metrics['Total Shipment Cost'], 1),
                        'Total Baseline Cost': round(metrics['Total Baseline Cost'], 1),
                        'Cost Savings': metrics['Cost Savings'],
                        'Percent Savings': round(metrics['Percent Savings'], 1),
                        'Average Utilization': round(metrics['Average Utilization'], 1),
                        'CO2 Emission': round(metrics['CO2 Emission'], 1)
                    }
                    
                    # Add columns for days relative to shipped date
                    for i in range(shipment_window + 1):
                        column_name = f'orders%_shipping_{i}day_early'
                        result[column_name] = distribution_percentage.get(i, 0)
                    
                    all_results.append(result)
                
                    # Update best results if current combination is better
                    if best_metrics is None or metrics['Cost Savings'] > best_metrics['Cost Savings']:
                        best_metrics = metrics
                        best_consolidated_shipments = all_consolidated_shipments
                        best_params = (shipment_window, high_priority_limit, utilization_threshold)
                
                
            
            end_time = time.time()
            time_taken = end_time - start_time
            time_taken_minutes = int(time_taken // 60)
            time_taken_seconds = int(time_taken % 60)
            st.write(f"Time taken: {time_taken_minutes} minutes {time_taken_seconds} seconds")            
                
            # Display best results
            st.markdown("<h2 style='font-size:26px;'>Best Simulation Results</h2>", unsafe_allow_html=True)
            st.write(f"Best Parameters: Shipment Window = {best_params[0]}, Utilization Threshold = {best_params[2]}%")
            
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                create_metric_box("Total Orders", f"{best_metrics['  Total Orders']:,}")
            with col2:
                create_metric_box("Total Shipments", f"{best_metrics['Total Shipments']:,}")
            with col3:
                create_metric_box("Cost Savings", f"£{best_metrics['Cost Savings']:,.1f}")
            with col4:
                create_metric_box("Percentage Savings", f"{best_metrics['Percent Savings']:.1f}%")
            with col5:
                create_metric_box("Average Utilization", f"{best_metrics['Average Utilization']:.1f}%")
            with col6:
                create_metric_box("CO2 Reduction (Kg)", f"{best_metrics['CO2 Emission']:,.1f}")
                
    
            # Display charts for best results
            st.plotly_chart(create_utilization_chart(best_consolidated_shipments))
            
            
            fig_pallet_distribution = create_pallet_distribution_chart(all_consolidated_shipments, total_shipment_capacity)
            st.plotly_chart(fig_pallet_distribution)

            # Create a dataframe with all simulation results
            results_df = pd.DataFrame(all_results)
            
            
            # Preprocess the data to keep only the row with max Cost Savings for each Shipment Window and Utilization Threshold
            optimal_results = results_df.loc[results_df.groupby(['Shipment Window', 'Utilization Threshold'])['Cost Savings'].idxmax()]
            
            # Create ColumnDataSource
            source = ColumnDataSource(optimal_results)
            
            # Create color mapper for Cost Savings (green for high savings, red for low savings)
            color_mapper = linear_cmap(field_name='Cost Savings', palette=RdYlGn11[::-1], 
                                       low=optimal_results['Cost Savings'].min(), high=optimal_results['Cost Savings'].max())
            
            st.markdown("<h2 style='font-size:24px;'>Optimal Simulation Results: Shipment Window vs Utilization Threshold</h2>", unsafe_allow_html=True)

            # Create the figure
            p = figure(x_axis_label='Utilization Threshold (%)',
                       y_axis_label='Shipment Window',
                       width=800, height=600)
            
            # Add the scatter plot
            scatter = p.scatter('Utilization Threshold', 'Shipment Window', source=source,
                                size=10, color=color_mapper, alpha=0.8)
            
            # Add color bar
            color_bar = ColorBar(color_mapper=color_mapper['transform'], width=8, location=(0,0),
                                 title="Cost Savings (£)")
            p.add_layout(color_bar, 'right')
            
            # Add hover tool
            hover = HoverTool(tooltips=[
                ('Shipment Window', '@{Shipment Window}'),
                ('Utilization Threshold', '@{Utilization Threshold}%'),
                ('Total Shipment Cost', '£@{Total Shipment Cost}{,.1f}'),
                ('Cost Savings', '£@{Cost Savings}{,.1f}'),
                ('Percent Savings', '@{Percent Savings}{,.1f}%'),
                ('Total Shipments', '@{Total Shipments}'),
                ('Average Utilization', '@{Average Utilization}{,.1f}%'),
                ('CO2 Emission', '@{CO2 Emission}{,.1f}'),
                ('Orders Shipping 0 Day Early', '@{orders%_shipping_0day_early}{,.1f}%'),
            ])
            p.add_tools(hover)
            
            # Add labels for Percent Savings
            labels = p.text('Utilization Threshold', 'Shipment Window', text='Percent Savings',
                            x_offset=5, y_offset=5, source=source, text_font_size='8pt')
            
            # Display the plot in Streamlit
            st.bokeh_chart(p, use_container_width=True)


            
            # Display the Shipment Window Comparison chart
            st.markdown("<h2 style='font-size:24px;'>Shipment Window Comparison</h2>", unsafe_allow_html=True)

            # Select the best rows for each shipment window
            best_results = results_df.loc[results_df.groupby('Shipment Window')['Percent Savings'].idxmax()]
            
            # Sort by Shipment Window
            best_results = best_results.sort_values('Shipment Window')
            
            # Create a complete range of shipment windows from 1 to 30
            all_windows = list(range(1, 31))
            
            # Create the subplot figure
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            
            # Add the stacked bar chart
            fig.add_trace(
                go.Bar(
                    x=all_windows,
                    y=[best_results[best_results['Shipment Window'] == w]['Total Shipment Cost'].values[0] if w in best_results['Shipment Window'].values else 0 for w in all_windows],
                    name='Total Shipment Cost',
                    marker_color='#1f77b4'
                )
            )
            
            fig.add_trace(
                go.Bar(
                    x=all_windows,
                    y=[best_results[best_results['Shipment Window'] == w]['Cost Savings'].values[0] if w in best_results['Shipment Window'].values else 0 for w in all_windows],
                    name='Cost Savings',
                    marker_color='#a9d6a9'
                )
            )
            
            # Add the line chart for Total Shipments on secondary y-axis
            fig.add_trace(
                go.Scatter(
                    x=all_windows,
                    y=[best_results[best_results['Shipment Window'] == w]['Total Shipments'].values[0] if w in best_results['Shipment Window'].values else None for w in all_windows],
                    name='Total Shipments',
                    mode='lines+markers',
                    line=dict(color='#ff7f0e', width=2),
                    marker=dict(size=8),
                    hovertemplate='<b>Shipment Window</b>: %{x}<br>' +
                                  '<b>Total Shipments</b>: %{y}<br>' +
                                  '<b>Average Utilization</b>: %{text:.1f}%<extra></extra>',
                    text=[best_results[best_results['Shipment Window'] == w]['Average Utilization'].values[0] if w in best_results['Shipment Window'].values else None for w in all_windows],
                ),
                secondary_y=True
            )
            
            # Add text annotations for Percent Savings
            for w in all_windows:
                if w in best_results['Shipment Window'].values:
                    row = best_results[best_results['Shipment Window'] == w].iloc[0]
                    fig.add_annotation(
                        x=w,
                        y=row['Total Shipment Cost'] + row['Cost Savings'],
                        text=f"{row['Percent Savings']:.1f}%",
                        showarrow=False,
                        yanchor='bottom',
                        yshift=5,
                        font=dict(size=10)
                    )
            
            # Update the layout
            fig.update_layout(
                barmode='stack',
                height=600,
                margin=dict(l=50, r=50, t=40, b=20),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
            )
            
            fig.update_xaxes(title_text='Shipment Window', tickmode='linear', dtick=1, range=[0.5, 30.5])
            fig.update_yaxes(title_text='Cost (£)', secondary_y=False)
            fig.update_yaxes(title_text='Total Shipments', secondary_y=True)
            
            # Show the chart
            st.plotly_chart(fig, use_container_width=True)
            
            
            # Display table with all results
            results_df = results_df.sort_values(by=['Percent Savings', 'Shipment Window', 'Utilization Threshold'], ascending=[False, True, True]).reset_index(drop=True).set_index('Shipment Window')
            st.markdown("<h2 style='font-size:24px;'>All Simulation Results</h2>", unsafe_allow_html=True)

            
            # Custom CSS to set the height of the dataframe
            custom_css = """
                <style>
                    .stDataFrame {
                        max-height: 250px;
                        overflow-y: auto;
                    }
                </style>
            """
            st.markdown(custom_css, unsafe_allow_html=True)
            
            # Display the dataframe
            st.dataframe(results_df)
            
            # Download results as CSV
            csv = results_df.to_csv(index=True)
            st.download_button(
                label="Download Simulation Results CSV",
                data=csv,
                file_name="simulation_results.csv",
                mime="text/csv",
            )
            

# Calculation tab
with tab2:
    st.markdown("<h2 style='font-size:24px;'>Calculation Parameters</h2>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    
    with col1:
        calc_shipment_window = st.slider("Shipment Window", min_value=0, max_value=30, value=4)
    
    with col2:
        calc_utilization_threshold = st.slider("Utilization Threshold", min_value=25, max_value=95, value=70, step=5)
    
    with col3:
        with st.expander("Iteration Breakdown", expanded=False):

            # Calculate the values dynamically
            total_days = (end_date - start_date).days + 1
            grouped = df.groupby(['PROD TYPE', group_field])
            total_groups = len(grouped)
            total_iterations = total_days * total_groups
        
            # Display the breakdown
            st.write(f"Total days to check consolidation: {total_days}")
            st.write(f"({start_date.date()} to {end_date.date()})")
            st.write(f"Total groups (PROD TYPE, {group_field}): {total_groups}")
            st.write(f"Total iterations: {total_iterations:,}")
    
    
    if st.button("Calculate"):
        start_time = time.time()
        
        with st.spinner("Calculating..."):
            # Prepare data for calculation
            df['GROUP'] = df[group_field]
            grouped = df.groupby(['PROD TYPE', 'GROUP'])
            date_range = pd.date_range(start=start_date, end=end_date)
            
            calc_high_priority_limit = 0
            all_consolidated_shipments = []
            all_allocation_matrices = []
            
            # Run calculation
            progress_bar = custom_progress_bar()
            
            for i, ((prod_type, group), group_df) in enumerate(grouped):
                consolidated_shipments, allocation_matrix = consolidate_shipments(
                    group_df, calc_high_priority_limit, calc_utilization_threshold, 
                    calc_shipment_window, date_range, lambda: None, total_shipment_capacity
                )
                all_consolidated_shipments.extend(consolidated_shipments)
                all_allocation_matrices.append(allocation_matrix)
                progress_percentage = int(((i + 1) / len(grouped)) * 100)
                progress_bar(progress_percentage)

            end_time = time.time()
            time_taken = end_time - start_time
            time_taken_minutes = int(time_taken // 60)
            time_taken_seconds = int(time_taken % 60)
            st.write(f"Time taken: {time_taken_minutes} minutes {time_taken_seconds} seconds")            
                
            # Calculate and display metrics
            metrics = calculate_metrics(all_consolidated_shipments, df)
            
            # Usage example:
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                create_metric_box("Total Orders", f"{metrics['Total Orders']:,}")
            with col2:
                create_metric_box("Total Shipments", f"{metrics['Total Shipments']:,}")
            with col3:
                create_metric_box("Cost Savings", f"£{metrics['Cost Savings']:,.1f}")
            with col4:
                create_metric_box("Percentage Savings", f"{metrics['Percent Savings']:.1f}%")
            with col5:
                create_metric_box("Average Utilization", f"{metrics['Average Utilization']:.1f}%")
            with col6:
                create_metric_box("CO2 Reduction (Kg)", f"{metrics['CO2 Emission']:,.1f}") 
                           
    
            # Add Calendar Chart
            st.markdown("<h2 style='font-size:24px;'>Shipments Calendar Heatmap</h2>", unsafe_allow_html=True)

            consolidated_df = pd.DataFrame(all_consolidated_shipments)
            heatmap_charts = create_heatmap_charts(consolidated_df, df)  # df is the original dataframe
            components.html(heatmap_charts.render_embed(), height=450, width=1050)
            
            
            # Display the consolidated shipments table
            st.markdown("<h2 style='font-size:24px;'>Consolidated Shipments Table</h2>", unsafe_allow_html=True)

            
            # Custom CSS to set the height of the dataframe
            custom_css = """
                <style>
                    .stDataFrame {
                        max-height: 250px;
                        overflow-y: auto;
                    }
                </style>
            """
            st.markdown(custom_css, unsafe_allow_html=True)
            
            consolidated_df['Date'] = pd.to_datetime(consolidated_df['Date'])

            # Format the 'Date' column to show only the date part
            consolidated_df['Date'] = consolidated_df['Date'].dt.date
            
            if group_method == 'Post Code Level':
                consolidated_df = consolidated_df.drop(columns=['GROUP'])
            else:  # Customer Level
                consolidated_df = consolidated_df.rename(columns={'GROUP': 'Customer'})
                        
            st.dataframe(consolidated_df.reset_index(drop=True).set_index('Date'))


            # Download consolidated shipments as CSV
            csv = consolidated_df.to_csv(index=False)
            st.download_button(
                label="Download Consolidated Shipments CSV",
                data=csv,
                file_name="consolidated_shipments.csv",
                mime="text/csv",
            )     
            
            # Display charts
            st.plotly_chart(create_utilization_chart(all_consolidated_shipments))
            
            
            # Add the new horizontal stacked bar chart with updated utilization categories
            st.markdown("<h2 style='font-size:24px;'>Utilization Categories</h2>", unsafe_allow_html=True)

            
            all_utilizations = [shipment['Utilization %'] for shipment in all_consolidated_shipments]
            total_shipments = len(all_consolidated_shipments)
            
            # Calculate pallet thresholds based on total_shipment_capacity
            high_utilization_pallets = round(0.65 * total_shipment_capacity)
            medium_utilization_pallets_low = round(0.25 * total_shipment_capacity)
            medium_utilization_pallets_high = round(0.65 * total_shipment_capacity)
            
            high_utilization = sum(1 for u in all_utilizations if u > 65)
            medium_utilization = sum(1 for u in all_utilizations if 25 < u <= 65)
            low_utilization = sum(1 for u in all_utilizations if u <= 25)
            
            utilization_data = [
                (f'High utilization (>65%, >{high_utilization_pallets} pallets)', high_utilization, high_utilization/total_shipments*100, '#1f77b4'),  # Dark blue
                (f'Medium utilization (25-65%, {medium_utilization_pallets_low}-{medium_utilization_pallets_high} pallets)', medium_utilization, medium_utilization/total_shipments*100, '#7fcdbb'),  # Medium blue
                (f'Low utilization (<=25%, <={medium_utilization_pallets_low} pallets)', low_utilization, low_utilization/total_shipments*100, '#c7e9b4')  # Light blue-green
            ]
            
            fig_utilization_categories = go.Figure()
            
            for category, value, percentage, color in utilization_data:
                fig_utilization_categories.add_trace(go.Bar(
                    y=['Utilization'],
                    x=[value],
                    name=category,
                    orientation='h',
                    text=[f'{value} ({percentage:.1f}%)'],
                    textposition='auto',
                    hoverinfo='text',
                    hovertext=[f'{category}<br>{value} shipments<br>{percentage:.1f}% of total'],
                    marker_color=color
                ))
            
            fig_utilization_categories.update_layout(
                barmode='stack',
                height=150,
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                xaxis=dict(title='Number of Shipments'),
                yaxis=dict(title=''),
            )
            
            st.plotly_chart(fig_utilization_categories, use_container_width=True)


            st.markdown("<h2 style='font-size:24px;'>Shipment Insights</h2>", unsafe_allow_html=True)

            # Truckload analysis
            full_truckloads = sum(1 for shipment in all_consolidated_shipments if shipment['Total Pallets'] > 26)
            partial_truckloads = metrics['Total Shipments'] - full_truckloads
            
            # Cost analysis
            avg_cost_per_pallet = metrics['Total Shipment Cost'] / metrics['Total Pallets']
            avg_cost_savings_per_shipment = metrics['Cost Savings'] / metrics['Total Shipments']
            
            # Calculate average pallets per shipment
            avg_pallets_per_shipment = metrics['Total Pallets'] / metrics['Total Shipments'] if metrics['Total Shipments'] > 0 else 0

            
            # Function to display metrics with custom font sizes
            def display_metric(title, value):
                st.markdown(f"""
                    <div style="
                        background-color: #c2dcff;
                        border: 2px solid #1f77b4;
                        border-radius: 15px;
                        padding: 0px 0px;
                        margin: 0px 0 0px 0;
                        text-align: center;
                        position: relative;
                        overflow: hidden;
                    ">
                        <h2 style="font-size: 18px; margin: 0 0 0px 0; color: #1f77b4; position: relative; z-index: 1;">{title}</h2>
                        <h2 style="font-size: 18px; margin: 0; color: #1f77b4; position: relative; z-index: 1;">{value}</h2>
                        <div style="
                            position: absolute;
                            top: 0;
                            left: 0;
                            right: 0;
                            bottom: 0;
                            background: transparent;
                            pointer-events: none;
                        "></div>
                    </div>
                """, unsafe_allow_html=True)
                           
            
            # Create columns for all 5 metrics in a single row
            col1, col2, col3, col4, col5 = st.columns(5)

            with col1:
                display_metric("Full Truckloads", f"{full_truckloads:,} ({full_truckloads/metrics['Total Shipments']*100:.1f}%)")
            with col2:
                display_metric("Partial Truckloads", f"{partial_truckloads:,} ({partial_truckloads/metrics['Total Shipments']*100:.1f}%)")
            with col3:
                display_metric("Cost per Pallet", f"£{avg_cost_per_pallet:.1f}")
            with col4:
                display_metric("Savings per Shipment", f"£{avg_cost_savings_per_shipment:.1f}")
            with col5:
                display_metric("Pallets per Shipment", f"{avg_pallets_per_shipment:.1f}")
              
                
            # Determine whether we're using Post Code or Customer level
            comparison_level = 'SHORT_POSTCODE' if group_method == 'Post Code Level' else 'NAME'
            
            # Group the consolidated shipments by the chosen level
            grouped_shipments = defaultdict(list)
            for shipment in all_consolidated_shipments:
                grouped_shipments[shipment['GROUP']].append(shipment)
            
            # Calculate metrics for each group
            group_metrics = []
            for group, shipments in grouped_shipments.items():
                total_pallets = sum(shipment['Total Pallets'] for shipment in shipments)
                total_orders = sum(len(shipment['Orders']) for shipment in shipments)
                total_shipments = len(shipments)
                
                # Calculate average utilization
                total_utilization = sum(shipment['Utilization %'] for shipment in shipments)
                avg_utilization = total_utilization / total_shipments if total_shipments > 0 else 0
                
                # Calculate baseline cost
                baseline_costs = []
                for shipment in shipments:
                    for order_id in shipment['Orders']:
                        order = df[df['ORDER_ID'] == order_id].iloc[0]
                        baseline_cost = get_baseline_cost(order['PROD TYPE'], order['SHORT_POSTCODE'], [order['Total Pallets']])
                        if not pd.isna(baseline_cost):
                            baseline_costs.append(baseline_cost)
                
                total_baseline_cost = sum(baseline_costs)
                total_shipment_cost = sum(shipment['Shipment Cost'] for shipment in shipments)
                cost_savings = total_baseline_cost - total_shipment_cost
                percent_savings = (cost_savings / total_baseline_cost) * 100 if total_baseline_cost > 0 else 0
                
                group_metrics.append({
                    'Group': group,
                    'Total Shipments': total_shipments,
                    'Total Orders': total_orders,
                    'Total Pallets': total_pallets,
                    'Baseline Cost': total_baseline_cost,
                    'Shipment Cost': total_shipment_cost,
                    'Cost Savings': cost_savings,
                    'Percent Savings': percent_savings,
                    'Average Utilization': avg_utilization
                })
            
            # Convert to DataFrame and sort by Cost Savings
            group_metrics_df = pd.DataFrame(group_metrics).sort_values('Cost Savings', ascending=False)
            
            # Round decimal columns to 1 place
            columns_to_round = ['Baseline Cost', 'Shipment Cost', 'Cost Savings', 'Percent Savings', 'Average Utilization']
            group_metrics_df[columns_to_round] = group_metrics_df[columns_to_round].round(1)

            # Select top 20 groups for display
            top_20_groups = group_metrics_df.head(20)
            

            st.write("")            
            
            # Create the comparison chart
            st.markdown(f"<h2 style='font-size:24px;'>{group_method} Comparison</h2>", unsafe_allow_html=True)

            
            
            # Set fixed chart dimensions and bar properties
            chart_width = 1200  # Fixed chart width in pixels
            chart_height = 600  # Fixed chart height in pixels
            max_bar_width = 0.8  # Maximum bar width in pixels
            min_gap_width = 5  # Minimum gap width in pixels
            
            # Calculate the bar width based on the number of groups
            total_groups = len(top_20_groups)
            available_width = chart_width - (total_groups + 1) * min_gap_width
            bar_width = min(max_bar_width, available_width / total_groups)
            
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            
            # Add the stacked bar chart
            fig.add_trace(
                go.Bar(
                    x=top_20_groups['Group'],
                    y=top_20_groups['Shipment Cost'],
                    name='Shipment Cost',
                    marker_color='#1f77b4',
                    width=bar_width
                )
            )
            
            fig.add_trace(
                go.Bar(
                    x=top_20_groups['Group'],
                    y=top_20_groups['Cost Savings'],
                    name='Cost Savings',
                    marker_color='#a9d6a9',
                    width=bar_width
                )
            )
            
            # Add the line chart for Total Shipments on secondary y-axis
            fig.add_trace(
                go.Scatter(
                    x=top_20_groups['Group'],
                    y=top_20_groups['Total Shipments'],
                    name='Total Shipments',
                    mode='lines+markers',
                    line=dict(color='#ff7f0e', width=2),
                    marker=dict(size=8),
                    hovertemplate='<b>%{x}</b><br>' +
                                  'Total Shipments: %{y}<br>' +
                                  'Average Utilization: %{text:.1f}%<extra></extra>',
                    text=top_20_groups['Average Utilization'],
                ),
                secondary_y=True
            )
            
            # Add text annotations for Percent Savings
            for i, row in top_20_groups.iterrows():
                fig.add_annotation(
                    x=row['Group'],
                    y=row['Shipment Cost'] + row['Cost Savings'],
                    text=f"{row['Percent Savings']:.1f}%",
                    showarrow=False,
                    yshift=10,
                    font=dict(size=10)
                )
            
            # Calculate the appropriate bargap
            total_bar_width = bar_width * total_groups
            total_gap_width = chart_width - total_bar_width
            bargap = total_gap_width / (total_groups - 1) / chart_width if total_groups > 1 else 0
            
            # Update the layout
            fig.update_layout(
                barmode='stack',
                height=chart_height,
                width=chart_width,
                margin=dict(l=50, r=50, t=40, b=100),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                xaxis_tickangle=-45,
                hovermode="x unified",
                bargap=bargap
            )
            
            fig.update_xaxes(title_text=group_method)
            fig.update_yaxes(title_text='Cost (£)', secondary_y=False)
            fig.update_yaxes(title_text='Total Shipments', secondary_y=True)
            
            # Show the chart
            st.plotly_chart(fig, use_container_width=True)
            
            # Display the first 5 rows of the detailed metrics in a scrollable table
            st.markdown(f"<h2 style='font-size:24px;'>Detailed {group_method} Metrics</h2>", unsafe_allow_html=True)

            
            # Custom CSS to set the height of the dataframe and make it scrollable
            custom_css = """
            <style>
                .stDataFrame {
                    max-height: 300px;
                    overflow-y: auto;
                }
            </style>
            """
            st.markdown(custom_css, unsafe_allow_html=True)
            
            # Display the first 5 rows of the dataframe
            st.dataframe(group_metrics_df.reset_index(drop=True).set_index('Group'))
            
            # Add a download button for the detailed metrics
            csv_detailed = group_metrics_df.to_csv(index=False)
            st.download_button(
                label=f"Download Detailed {group_method} Metrics CSV",
                data=csv_detailed,
                file_name=f"detailed_{group_method.lower().replace(' ', '_')}_metrics.csv",
                mime="text/csv",
            )


            fig_pallet_distribution = create_pallet_distribution_chart(all_consolidated_shipments, total_shipment_capacity)
            st.plotly_chart(fig_pallet_distribution)
            

            # Order count distribution
            order_count_distribution = Counter(shipment['Order Count'] for shipment in all_consolidated_shipments)
            order_count_percentages = {
                '1 order': (order_count_distribution[1] / metrics['Total Shipments']) * 100,
                '2 order': (order_count_distribution[2] / metrics['Total Shipments']) * 100,
                '3-5 orders': (sum(order_count_distribution[i] for i in range(3, 6)) / metrics['Total Shipments']) * 100,
                '6-10 orders': (sum(order_count_distribution[i] for i in range(6, 11)) / metrics['Total Shipments']) * 100,
                '>10 orders': (sum(order_count_distribution[i] for i in range(11, max(order_count_distribution.keys()) + 1)) / metrics['Total Shipments']) * 100
            }
            
            # Create the pie chart
            fig_order_count = px.pie(
                values=list(order_count_percentages.values()), 
                names=list(order_count_percentages.keys()),
                title='Order Count Distribution',
                color_discrete_sequence=px.colors.qualitative.Pastel  # Applying pastel color palette
            )
            
            # Update layout for fixed width and title size
            fig_order_count.update_layout(
                title={'text': 'Order Count Distribution', 'font': {'size': 22}},
                width=600  # Fixing the chart width to 600px
            )
            
            # Display the chart in Streamlit
            st.plotly_chart(fig_order_count)

            
    

# Add a section for data exploration
st.markdown("<h2 style='font-size:24px;'>Data Exploration</h2>", unsafe_allow_html=True)
if st.checkbox("Show raw data"):
    st.subheader("Raw data")
    df['SHIPPED_DATE'] = df['SHIPPED_DATE'].dt.date
    
    columns = df.columns.tolist()

    # Move 'PROD TYPE' to the front if it's not already there
    if 'PROD TYPE' in columns:
        columns.remove('PROD TYPE')
        columns = ['PROD TYPE'] + columns
    
    # Select the first 5 columns (including 'PROD TYPE' as the index)
    df_display = df[columns[:10]]
    
    # Display the first 5 columns
    st.write(df_display.reset_index(drop=True).set_index('PROD TYPE'))

    # Add download button for raw data
    csv = df.to_csv(index=False)
    st.download_button(
        label="Download Raw Data CSV",
        data=csv,
        file_name="raw_data.csv",
        mime="text/csv",
    )

# Add a histogram of pallets per order

fig_pallets = px.histogram(
    df, 
    x="Total Pallets", 
    nbins=50, 
    color_discrete_sequence=['#1f77b4']
)

# Update the layout to set width and height, and add gaps between bars
fig_pallets.update_layout(
    title={'text': "Distribution of Pallets per Order (Raw Data)", 'font': {'size': 22}},
    width=1000,
    height=500,
    bargap=0.2  # This adds a gap between bars. Adjust this value to increase or decrease the gap.
)

# Update the traces to change the color if desired
fig_pallets.update_traces(marker_color='#1f77b4')  # You can change this color as needed

# Display the chart without using container width
st.plotly_chart(fig_pallets)

st.sidebar.info('This dashboard provides insights into shipment consolidation for Perrigo. Use tabs to switch between simulation and calculation modes.')
