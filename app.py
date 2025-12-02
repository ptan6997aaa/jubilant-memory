from nicegui import ui, app
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

# ┌──────────────────────────────────────────────┐
# │ 1. DATA LOADING & PROCESSING                  │
# │ 目标：构建一个扁平化的分析型数据集（宽表）     │
# └──────────────────────────────────────────────┘

# 加载事实表（考试成绩记录）
df_fact = pd.read_excel("FactPerformance.xlsx", sheet_name="Sheet1")

# 加载维度表
df_dimStu = pd.read_excel("DimStudents.xlsx", sheet_name="Sheet1")    # 学生信息
df_dimCal = pd.read_excel("DimCalendar.xlsx", sheet_name="Date")      # 日期维度
df_dimSub = pd.read_excel("DimSubjects.xlsx", sheet_name="DimSubjects")  # 学科信息

# ── 关联维度表到事实表（星型模型展开） ───────────────────────────────
# 通过 StudentID 关联学生年级
df = pd.merge(df_fact, df_dimStu[["StudentID", "GradeLevel"]], on="StudentID", how="left")
# 通过 SubjectID 关联学科名称
df = pd.merge(df, df_dimSub[["SubjectID", "SubjectName"]], on="SubjectID", how="left")

# ── 日期维度增强：构造可读的时间标签 ───────────────────────────────
# 例如：2023 Q1, 2023-03
df_dimCal["YearQuarterConcat"] = df_dimCal["Year"].astype(str) + " " + df_dimCal["QuarterNumber"].apply(lambda x: f"Q{x}")
df_dimCal["YearMonthConcat"] = df_dimCal["Year"].astype(str) + "-" + df_dimCal["Month"].apply(lambda x: f"{x:02d}")

# 通过 DateKey（如 20230301）关联日期维度
df = pd.merge(df, df_dimCal[["DateKey", "YearQuarterConcat", "YearMonthConcat", "QuarterNumber", "Year"]], on="DateKey", how="left")

# ── 衍生字段：支持加权计算和通过率 ─────────────────────────────────
# 若无权重列，设为 1（即所有考试等权）
if "Weight" not in df.columns:
    df["Weight"] = 1
# 预计算加权分数：用于后续加权平均
if "WeightedScore" not in df.columns:
    df["WeightedScore"] = df["Score"] * df["Weight"]

# 判断是否及格（>=55 为 Pass）
df["PassedScore"] = df["Score"].apply(lambda x: "Pass" if x >= 55 else "Fail")

# ── 成绩等级映射（A-F）──────────────────────────────────────────
def get_grade(score):
    """将数值分数映射为字母等级"""
    if score > 84: return "A"
    if score > 74: return "B"
    if score > 64: return "C"
    if score > 54: return "D"
    return "F"

df["Assessment_Grade"] = df["Score"].apply(get_grade)

# 设置有序分类（确保 A > B > C > D > F）
grade_order = ['A', 'B', 'C', 'D', 'F']
df['Assessment_Grade'] = pd.Categorical(df['Assessment_Grade'], categories=grade_order, ordered=True)

# 可选：按年级 + 成绩排序（便于后续分组）
if "GradeLevel" in df.columns:
    df = df.sort_values(['GradeLevel', 'Assessment_Grade'])


# ┌──────────────────────────────────────────────┐
# │ 2. DASHBOARD STATE & FILTER LOGIC             │
# │ 核心：全局状态管理 + 灵活的数据过滤函数        │
# └──────────────────────────────────────────────┘

# 全局筛选状态字典（驱动整个仪表板）
state = {
    'grade': 'All',      # 当前选中的成绩等级（A/B/C/D/F/All）
    'level': 'All',      # 当前选中的年级
    'time': 'All',       # 当前选中的时间（如 "2023 Q1" 或 "2023-03"）
    'subject': 'All',    # 当前选中的学科
    'view_mode': 'Quarter'  # 时间视图粒度（Quarter / Month）
}

def get_data(ignore_grade=False, ignore_level=False, ignore_time=False, ignore_subject=False):
    """
    根据当前 state 过滤数据，支持“忽略某个维度”（用于展示完整分布）。
    例如：画 Grade 分布图时，应忽略 grade 筛选，否则只显示一个 slice。
    """
    d = df.copy()
    
    # 应用成绩等级筛选（除非被忽略）
    if not ignore_grade and state['grade'] != 'All':
        d = d[d["Assessment_Grade"] == state['grade']]
    
    # 应用年级筛选
    if not ignore_level and state['level'] != 'All':
        d = d[d["GradeLevel"] == state['level']]
    
    # 应用学科筛选
    if not ignore_subject and state['subject'] != 'All':
        d = d[d["SubjectName"] == state['subject']]
    
    # 应用时间筛选：区分季度（含 'Q'）和月份（含 '-'）
    curr_time = state['time']
    if not ignore_time and curr_time != 'All':
        if 'Q' in curr_time:
            d = d[d["YearQuarterConcat"] == curr_time]
        else:
            d = d[d["YearMonthConcat"] == curr_time]
    
    return d


# ┌──────────────────────────────────────────────┐
# │ 3. UI BUILDER                                 │
# │ 使用 NiceGUI 构建响应式、交互式仪表板          │
# └──────────────────────────────────────────────┘

@ui.page('/')
def index():
    # ── 全局样式：自定义卡片和 KPI 样式 ─────────────────────────────
    ui.add_head_html('''
        <style>
            /* 渐变紫卡：用于 KPI */
            .card-purple { background: linear-gradient(45deg, #6a11cb 0%, #2575fc 100%); color: white; }
            /* KPI 标题样式 */
            .kpi-title { opacity: 0.8; font-size: 0.9rem; font-weight: 500; }
            /* KPI 数值样式 */
            .kpi-value { font-size: 2rem; font-weight: bold; }
        </style>
    ''')

    # ── 顶部标题栏 ───────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-4'):
        ui.label('Education Performance Analysis').classes('text-2xl font-bold text-gray-800')
        status_label = ui.label()  # 显示当前筛选状态
        ui.button('Reset All Filters', on_click=lambda: reset_filters()).classes('bg-gray-500 text-white')

    # ── KPI 行：4 个关键指标卡片 ───────────────────────────────────
    with ui.grid(columns=4).classes('w-full gap-4 mb-6'):
        # 平均分
        with ui.card().classes('card-purple'):
            ui.label('Average Score').classes('kpi-title')
            kpi_avg = ui.label('0.00').classes('kpi-value')
        # 加权平均分
        with ui.card().classes('card-purple'):
            ui.label('Weighted Avg').classes('kpi-title')
            kpi_weighted = ui.label('0.00%').classes('kpi-value')
        # 通过率
        with ui.card():
            ui.label('Pass Rate').classes('text-green-600 font-medium')
            kpi_pass = ui.label('0.00%').classes('text-green-600 text-3xl font-bold')
        # 满分率
        with ui.card():
            ui.label('Perfect Scores').classes('text-blue-600 font-medium')
            kpi_perfect = ui.label('0.0%').classes('text-blue-600 text-3xl font-bold')

    # ── 第一行图表：成绩等级 + 年级分布（2 列）───────────────────────
    with ui.grid(columns=2).classes('w-full gap-6 mb-6'):
        # 成绩等级分布（饼图）
        with ui.card().classes('w-full h-80'):
            ui.label('Grade Distribution').classes('font-bold text-gray-700 mb-2')
            plot_grade = ui.plotly({}).classes('w-full h-full')

        # 年级学生分布（饼图）
        with ui.card().classes('w-full h-80'):
            ui.label('Level Distribution').classes('font-bold text-gray-700 mb-2')
            plot_level = ui.plotly({}).classes('w-full h-full')

    # ── 第二行图表：时间趋势 + 学科对比（2 列）───────────────────────
    with ui.grid(columns=2).classes('w-full gap-6'):
        # 时间趋势图（带粒度切换）
        with ui.card().classes('w-full h-96'):
            with ui.row().classes('w-full items-center justify-between'):
                time_title_label = ui.label('Performance Over Time').classes('font-bold text-gray-700')
                # 切换 Quarter / Month 视图
                view_toggle = ui.toggle(['Quarter', 'Month'], value='Quarter', on_change=lambda: update_dashboard()).props('no-caps')
            plot_time = ui.plotly({}).classes('w-full h-full')

        # 学科平均分（点击可筛选）
        with ui.card().classes('w-full h-96'):
            ui.label('Score by Subject (Click to Filter)').classes('font-bold text-gray-700 mb-2')
            plot_subject = ui.plotly({}).classes('w-full h-full')


    # ┌──────────────────────────────────────────────────────────┐
    # │ MAIN UPDATE LOGIC: update_dashboard()                     │
    # │ 每次筛选变化时，重新计算所有 KPI 和图表                    │
    # └──────────────────────────────────────────────────────────┘
    def update_dashboard():
        """统一刷新所有 UI 元素"""

        # 1. 更新顶部状态标签（显示当前筛选条件）
        status_label.set_text(f"Filters | Grade: {state['grade']} | Level: {state['level']} | Time: {state['time']} | Sub: {state['subject']}")

        # 2. ── 计算 KPI（应用全部筛选）──────────────────────────────
        d_kpi = get_data()  # 不忽略任何维度
        if d_kpi.empty:
            # 安全处理空数据
            kpi_avg.set_text("0.00")
            kpi_weighted.set_text("0.00%")
            kpi_pass.set_text("0.00%")
            kpi_perfect.set_text("0.0%")
        else:
            # 平均分
            kpi_avg.set_text(f"{d_kpi['Score'].mean():.2f}")
            # 加权平均分（注意：若总权重为0则避免除零）
            w_sum = d_kpi["Weight"].sum()
            val_w = (d_kpi["WeightedScore"].sum() / w_sum) if w_sum > 0 else 0
            kpi_weighted.set_text(f"{(val_w * 100 if val_w <= 1 else val_w):.2f}%")
            # 通过率
            pass_rate = len(d_kpi[d_kpi['PassedScore'] == 'Pass']) / len(d_kpi) * 100
            kpi_pass.set_text(f"{pass_rate:.2f}%")
            # 满分率：自动判断满分是 100 还是 1.0
            target = 100 if df["Score"].max() > 1.0 else 1.0
            perfect_rate = len(d_kpi[d_kpi['Score'] == target]) / len(d_kpi) * 100
            kpi_perfect.set_text(f"{perfect_rate:.1f}%")

        # 3. ── 成绩等级分布图（忽略 grade 筛选）──────────────────────
        d_grade = get_data(ignore_grade=True)
        if not d_grade.empty:
            # 按等级分组，统计考试次数
            df_agg = d_grade.groupby('Assessment_Grade', observed=False)['Score'].count().reset_index()
            fig = px.pie(
                df_agg, values='Score', names='Assessment_Grade', hole=0.6,
                color='Assessment_Grade',
                color_discrete_map={'A': '#2ca02c', 'B': '#1f77b4', 'C': '#ff7f0e', 'D': '#d62728', 'F': '#7f7f7f'}
            )
            # 高亮当前选中的等级
            if state['grade'] != 'All':
                fig.update_traces(pull=[0.1 if x == state['grade'] else 0 for x in df_agg['Assessment_Grade']])
            # 中心标注：当前筛选下的总考试数
            fig.add_annotation(text=f"{len(d_kpi):,}<br>Tests", x=0.5, y=0.5, showarrow=False, font_size=16)
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            plot_grade.update_figure(fig)
        else:
            plot_grade.update_figure(go.Figure())  # 清空图表

        # 4. ── 年级分布图（忽略 level 筛选）────────────────────────
        d_level = get_data(ignore_level=True)
        if not d_level.empty:
            # 按年级分组，统计唯一学生数（非考试次数！）
            df_agg = d_level.groupby('GradeLevel', observed=False)['StudentID'].nunique().reset_index()
            fig = px.pie(df_agg, values='StudentID', names='GradeLevel', hole=0.6, color='GradeLevel')
            # 高亮当前选中年级
            if state['level'] != 'All':
                fig.update_traces(pull=[0.1 if x == state['level'] else 0 for x in df_agg['GradeLevel']])
            # 中心标注：当前筛选下的唯一学生数
            fig.add_annotation(text=f"{d_kpi['StudentID'].nunique():,}<br>Students", x=0.5, y=0.5, showarrow=False, font_size=16)
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            plot_level.update_figure(fig)
        else:
            plot_level.update_figure(go.Figure())

        # 5. ── 时间趋势图（忽略 time 筛选）──────────────────────────
        d_time = get_data(ignore_time=True)
        mode = state['view_mode']  # 'Quarter' 或 'Month'

        # 下钻上下文处理：若当前在月视图且之前选了季度，则只显示该季度的月份
        if mode == "Month":
            if state['time'] != "All" and 'Q' in state['time']:
                # 用户点击了季度 → 只显示该季度的月份
                d_time = d_time[d_time["YearQuarterConcat"] == state['time']]
            elif state['time'] != "All" and '-' in state['time']:
                # 安全查找：避免 KeyError
                matches = df[df["YearMonthConcat"] == state['time']]
                if not matches.empty:
                    parent_quarter = matches["YearQuarterConcat"].iloc[0]
                    d_time = d_time[d_time["YearQuarterConcat"] == parent_quarter]

        # 动态更新标题
        if mode == "Month" and 'Q' in state['time']:
            time_title_label.set_text(f"Monthly Breakdown for {state['time']}")
        else:
            time_title_label.set_text("Performance Over Time")

        if not d_time.empty:
            # 选择分组字段（季度 or 月）
            col_group = "YearQuarterConcat" if mode == "Quarter" else "YearMonthConcat"
            # 计算每个时间段的平均分
            df_bar = d_time.groupby(col_group)["Score"].mean().reset_index().sort_values(col_group)
            # 绘制柱状图（强制 x 轴为分类，避免 Plotly 自动解析为日期）
            fig = px.bar(df_bar, x=col_group, y="Score", text_auto='.1f')
            fig.update_xaxes(type='category')
            # 高亮当前选中的时间点（除非正在下钻季度→月）
            opacities = [1.0] * len(df_bar)
            if state['time'] != 'All':
                if not (mode == "Month" and 'Q' in state['time']):
                    opacities = [1.0 if x == state['time'] else 0.3 for x in df_bar[col_group]]
            fig.update_traces(marker=dict(opacity=opacities))
            # 添加全局平均分参考线（基于当前筛选）
            glob_avg = get_data()['Score'].mean() if not get_data().empty else 0
            fig.add_hline(y=glob_avg, line_dash="dash", line_color="red")
            fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), xaxis_title=None, yaxis_title="Avg Score")
            plot_time.update_figure(fig)
        else:
            plot_time.update_figure(go.Figure())

        # 6. ── 学科平均分图（忽略 subject 筛选）─────────────────────
        d_sub = get_data(ignore_subject=True)
        if not d_sub.empty:
            # 按学科分组，计算平均分，并按分数降序排列
            df_bar = d_sub.groupby("SubjectName")["Score"].mean().reset_index().sort_values("Score", ascending=False)
            fig = px.bar(df_bar, x="SubjectName", y="Score", text_auto='.1f')
            # 高亮当前选中学科
            if state['subject'] != 'All':
                opacities = [1.0 if x == state['subject'] else 0.3 for x in df_bar["SubjectName"]]
                fig.update_traces(marker=dict(opacity=opacities))
            # 添加全局平均分参考线
            glob_avg = get_data()['Score'].mean() if not get_data().empty else 0
            fig.add_hline(y=glob_avg, line_dash="dash", line_color="red")
            fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), xaxis_title=None, yaxis_title="Avg Score")
            plot_subject.update_figure(fig)
        else:
            plot_subject.update_figure(go.Figure())


    # ┌──────────────────────────────────────────────────────────┐
    # │ EVENT HANDLERS: 用户交互逻辑                               │
    # └──────────────────────────────────────────────────────────┘

    def reset_filters():
        """重置所有筛选器到默认状态"""
        state.update({
            'grade': 'All',
            'level': 'All',
            'time': 'All',
            'subject': 'All',
            'view_mode': 'Quarter'
        })
        view_toggle.value = 'Quarter'
        update_dashboard()

    # ── 图表点击事件：实现联动筛选 ─────────────────────────────────
    def handle_click_grade(e):
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['label']  # 获取点击的等级
            # 切换：若已选中则取消，否则选中
            state['grade'] = 'All' if state['grade'] == clicked else clicked
            update_dashboard()

    def handle_click_level(e):
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['label']
            state['level'] = 'All' if state['level'] == clicked else clicked
            update_dashboard()

    def handle_click_time(e):
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['x']
            # Plotly 可能返回完整日期（如 "2023-01-01"），需截断为 "2023-01"
            if isinstance(clicked, str) and len(clicked) > 7 and clicked[4] == '-':
                clicked = clicked[:7]
            # 钻取逻辑：点击季度 → 进入月视图
            if state['view_mode'] == 'Quarter':
                state['time'] = clicked
                state['view_mode'] = 'Month'
                view_toggle.value = 'Month'
            else:
                state['time'] = 'All' if state['time'] == clicked else clicked
            update_dashboard()

    def handle_click_subject(e):
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['x']
            state['subject'] = 'All' if state['subject'] == clicked else clicked
            update_dashboard()

    # 绑定点击事件到每个 Plotly 图表
    plot_grade.on('plotly_click', handle_click_grade)
    plot_level.on('plotly_click', handle_click_level)
    plot_time.on('plotly_click', handle_click_time)
    plot_subject.on('plotly_click', handle_click_subject)

    # 绑定时间粒度切换事件
    def on_view_change(e):
        state['view_mode'] = e.value
        update_dashboard()
    view_toggle.on_value_change(on_view_change)

    # ── 初始化仪表板 ────────────────────────────────────────────
    update_dashboard()


# ── 启动应用 ──────────────────────────────────────────────────
ui.run(title="Education Dashboard", port=8080)