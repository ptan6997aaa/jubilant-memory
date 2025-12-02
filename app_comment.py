from nicegui import ui
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ┌──────────────────────────────────────────────────────────────────────────────┐
# │ 1. DATA LOADING: 全局只读数据初始化                                           │
# │                                                                              │
# │ ★ 关键设计：                                                                 │
# │   - 所有原始数据（df）在模块加载时一次性读入，作为**只读全局变量**           │
# │   - 节省内存：1000 个用户共享同一份基础数据                                 │
# │   - 安全性：每个用户操作的是 df.copy() 的副本，不会污染原始数据              │
# │   - 性能：避免每次请求重复读取 Excel（I/O 昂贵）                            │
# └──────────────────────────────────────────────────────────────────────────────┘

# 加载事实表（考试成绩记录）
df_fact = pd.read_excel("FactPerformance.xlsx", sheet_name="Sheet1")

# 加载维度表
df_dimStu = pd.read_excel("DimStudents.xlsx", sheet_name="Sheet1")    # 学生信息
df_dimCal = pd.read_excel("DimCalendar.xlsx", sheet_name="Date")      # 日期维度
df_dimSub = pd.read_excel("DimSubjects.xlsx", sheet_name="DimSubjects")  # 学科信息

# ── 构建分析宽表：星型模型展开（Kimball 维度建模）──────────────────────────────
# 通过 StudentID 关联学生年级（维度退化）
df = pd.merge(df_fact, df_dimStu[["StudentID", "GradeLevel"]], on="StudentID", how="left")
# 通过 SubjectID 关联学科名称
df = pd.merge(df, df_dimSub[["SubjectID", "SubjectName"]], on="SubjectID", how="left")

# ── 增强日期维度：构造业务友好的时间标签 ───────────────────────────────────────
# 示例: Year=2023, QuarterNumber=1 → "2023 Q1"
df_dimCal["YearQuarterConcat"] = df_dimCal["Year"].astype(str) + " " + df_dimCal["QuarterNumber"].apply(lambda x: f"Q{x}")
# 示例: Year=2023, Month=3 → "2023-03"
df_dimCal["YearMonthConcat"] = df_dimCal["Year"].astype(str) + "-" + df_dimCal["Month"].apply(lambda x: f"{x:02d}")

# 通过 DateKey（如 20230301）将日期维度关联到事实表
df = pd.merge(df, df_dimCal[["DateKey", "YearQuarterConcat", "YearMonthConcat", "QuarterNumber", "Year"]], on="DateKey", how="left")

# ── 衍生指标：支持灵活分析 ───────────────────────────────────────────────────
# 权重字段：若缺失则默认为 1（等权处理）
if "Weight" not in df.columns:
    df["Weight"] = 1
# 预计算加权分数 = 原始分数 × 权重（避免重复计算）
if "WeightedScore" not in df.columns:
    df["WeightedScore"] = df["Score"] * df["Weight"]

# 通过率判定：分数 ≥ 55 为通过（业务规则）
df["PassedScore"] = df["Score"].apply(lambda x: "Pass" if x >= 55 else "Fail")

# ── 成绩等级映射（A-F）：支持有序分类分析 ──────────────────────────────────────
def get_grade(score):
    """将数值分数映射为字母等级（业务规则）"""
    if score > 84: return "A"
    if score > 74: return "B"
    if score > 64: return "C"
    if score > 54: return "D"
    return "F"

df["Assessment_Grade"] = df["Score"].apply(get_grade)

# 设置为有序分类类型（确保 A > B > C > D > F，影响排序和分组）
grade_order = ['A', 'B', 'C', 'D', 'F']
df['Assessment_Grade'] = pd.Categorical(df['Assessment_Grade'], categories=grade_order, ordered=True)

# 可选排序：按年级 + 成绩等级排序，便于后续分组展示（非必需，但提升可读性）
if "GradeLevel" in df.columns:
    df = df.sort_values(['GradeLevel', 'Assessment_Grade'])


# ┌──────────────────────────────────────────────────────────────────────────────┐
# │ 2. DASHBOARD CLASS: 核心交互式仪表板类                                        │
# │                                                                              │
# │ ★ 架构优势：                                                                 │
# │   - 每个用户会话拥有独立的 Dashboard 实例                                    │
# │   - 完全隔离：用户A的筛选不影响用户B                                        │
# │   - 高内聚：状态 + 数据逻辑 + UI + 事件处理 封装在一起                       │
# │   - 易测试：可实例化并调用方法验证逻辑                                       │
# │   - 易维护：修改一个图表不影响其他部分                                       │
# └──────────────────────────────────────────────────────────────────────────────┘

class Dashboard:
    def __init__(self):
        # ── 状态管理：每个实例维护独立的筛选状态 ───────────────────────────────────
        #   - 'All' 表示未筛选
        #   - 值如 'A', 'Grade 9', '2023 Q1', 'Math'
        self.state = {
            'grade': 'All',      # 成绩等级筛选
            'level': 'All',      # 年级筛选
            'time': 'All',       # 时间筛选（季度/月）
            'subject': 'All',    # 学科筛选
            'view_mode': 'Quarter'  # 时间视图粒度（驱动时间图展示逻辑）
        }
        
        # ── UI 元素引用：占位符，将在 build() 中绑定到实际 NiceGUI 组件 ───────────
        #   - 使用 self.xxx 避免全局变量
        #   - 每个实例拥有自己的 UI 元素集合
        self.kpi_avg = None
        self.kpi_weighted = None
        self.kpi_pass = None
        self.kpi_perfect = None
        
        self.plot_grade = None
        self.plot_level = None
        self.plot_time = None
        self.plot_subject = None
        
        self.status_label = None      # 顶部状态文本
        self.time_title_label = None  # 时间图标题（动态更新）
        self.view_toggle = None       # 时间粒度切换控件

    # ── 数据过滤核心：基于当前状态返回筛选后数据副本 ──────────────────────────────
    def get_data(self, ignore_grade=False, ignore_level=False, ignore_time=False, ignore_subject=False):
        """
        根据 self.state 过滤全局数据 df，返回副本。
        
        参数说明：
          - ignore_xxx=True：在渲染某维度分布图时，需忽略该维度的筛选，
            以展示完整分布（例如：渲染 Grade 图时，应忽略 grade 筛选）
        返回：
          - pd.DataFrame：筛选后的数据副本（安全，可修改）
        """
        d = df.copy()  # ★ 关键：返回副本！确保多用户安全
        
        # 应用成绩等级筛选（除非被忽略）
        if not ignore_grade and self.state['grade'] != 'All':
            d = d[d["Assessment_Grade"] == self.state['grade']]
        
        # 应用年级筛选
        if not ignore_level and self.state['level'] != 'All':
            d = d[d["GradeLevel"] == self.state['level']]
        
        # 应用学科筛选
        if not ignore_subject and self.state['subject'] != 'All':
            d = d[d["SubjectName"] == self.state['subject']]
        
        # 应用时间筛选：区分季度（含 'Q'）和月份（含 '-'）
        curr_time = self.state['time']
        if not ignore_time and curr_time != 'All':
            if 'Q' in curr_time:
                d = d[d["YearQuarterConcat"] == curr_time]
            else:
                d = d[d["YearMonthConcat"] == curr_time]
        
        return d

    # ── KPI 渲染：4 个关键指标卡片 ───────────────────────────────────────────────
    def render_kpis(self):
        """
        渲染顶部 4 个 KPI 卡片。
        - 数据：应用全部筛选条件（不忽略任何维度）
        - 安全处理：空数据时显示默认值
        """
        d_kpi = self.get_data()  # 应用全部筛选
        
        if d_kpi.empty:
            # 安全兜底：避免除零或 NaN
            self.kpi_avg.set_text("0.00")
            self.kpi_weighted.set_text("0.00%")
            self.kpi_pass.set_text("0.00%")
            self.kpi_perfect.set_text("0.0%")
            return

        # 1. 平均分：简单算术平均
        self.kpi_avg.set_text(f"{d_kpi['Score'].mean():.2f}")
        
        # 2. 加权平均分：sum(WeightedScore) / sum(Weight)
        w_sum = d_kpi["Weight"].sum()
        val_w = (d_kpi["WeightedScore"].sum() / w_sum) if w_sum > 0 else 0
        # 自动判断是否为百分比（若 ≤1 则 ×100）
        weighted_display = (val_w * 100 if val_w <= 1 else val_w)
        self.kpi_weighted.set_text(f"{weighted_display:.2f}%")
        
        # 3. 通过率：Pass 记录占比
        pass_rate = (d_kpi['PassedScore'] == 'Pass').mean() * 100
        self.kpi_pass.set_text(f"{pass_rate:.2f}%")
        
        # 4. 满分率：自动判断满分是 100 还是 1.0
        target = 100 if df["Score"].max() > 1.0 else 1.0
        perfect_rate = (d_kpi['Score'] == target).mean() * 100
        self.kpi_perfect.set_text(f"{perfect_rate:.1f}%")

    # ── 成绩等级分布图（环形饼图）───────────────────────────────────────────────
    def render_grade_chart(self):
        """
        渲染成绩等级分布图。
        三步法：
          1. 忽略 grade 筛选 → 获取完整等级分布
          2. 按 Assessment_Grade 分组计数（考试次数）
          3. 绘制环形饼图，高亮当前选中项，中心标注总考试数
        """
        d = self.get_data(ignore_grade=True)  # STEP 1: 忽略自身维度
        
        if d.empty:
            self.plot_grade.update_figure(go.Figure())  # 清空图表
            return
            
        # STEP 2: 聚合 + 绘图
        df_agg = d.groupby('Assessment_Grade', observed=False)['Score'].count().reset_index()
        fig = px.pie(
            df_agg, values='Score', names='Assessment_Grade', hole=0.6,
            color='Assessment_Grade',
            # 固定颜色映射，确保 A 始终绿色，F 灰色
            color_discrete_map={'A': '#2ca02c', 'B': '#1f77b4', 'C': '#ff7f0e', 'D': '#d62728', 'F': '#7f7f7f'}
        )
        # 高亮当前选中的等级（拉出效果）
        if self.state['grade'] != 'All':
            fig.update_traces(pull=[0.1 if x == self.state['grade'] else 0 for x in df_agg['Assessment_Grade']])
            
        # 中心标注：当前筛选下的总考试数（来自完整筛选数据）
        d_total = self.get_data()
        fig.add_annotation(text=f"{len(d_total):,}<br>Tests", x=0.5, y=0.5, showarrow=False, font_size=16)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
        self.plot_grade.update_figure(fig)

    # ── 年级分布图（环形饼图）───────────────────────────────────────────────────
    def render_level_chart(self):
        """
        渲染年级学生分布图。
        - 统计维度：唯一学生数（nunique），非考试次数！
        - 高亮当前选中年级
        - 中心标注：当前筛选下的唯一学生总数
        """
        d = self.get_data(ignore_level=True)
        if d.empty:
            self.plot_level.update_figure(go.Figure())
            return
            
        df_agg = d.groupby('GradeLevel', observed=False)['StudentID'].nunique().reset_index()
        fig = px.pie(df_agg, values='StudentID', names='GradeLevel', hole=0.6, color='GradeLevel')
        if self.state['level'] != 'All':
            fig.update_traces(pull=[0.1 if x == self.state['level'] else 0 for x in df_agg['GradeLevel']])
            
        d_total = self.get_data()
        fig.add_annotation(text=f"{d_total['StudentID'].nunique():,}<br>Students", x=0.5, y=0.5, font_size=16, showarrow=False)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
        self.plot_level.update_figure(fig)

    # ── 时间趋势图（柱状图 + 下钻支持）───────────────────────────────────────────
    def render_time_chart(self):
        """
        渲染时间趋势图，支持季度/月切换和下钻。
        核心逻辑：
          - 忽略 time 筛选以展示完整时间序列
          - 若当前在月视图且之前选了季度，则只显示该季度的月份（下钻上下文）
          - 动态更新标题（如 "Monthly Breakdown for 2023 Q1"）
          - 高亮当前选中时间点
          - 添加全局平均分参考线
        """
        d = self.get_data(ignore_time=True)
        mode = self.state['view_mode']  # 'Quarter' 或 'Month'
        
        # 下钻上下文处理：若在月视图且 state['time'] 是季度，则只显示该季度的月份
        if mode == "Month":
            if self.state['time'] != "All" and 'Q' in self.state['time']:
                # 用户点击了季度柱 → 下钻到该季度的月份
                d = d[d["YearQuarterConcat"] == self.state['time']]
            elif self.state['time'] != "All" and '-' in self.state['time']:
                # 安全查找：避免 KeyError（当 time 为月份时，找其所属季度）
                matches = df[df["YearMonthConcat"] == self.state['time']]
                if not matches.empty:
                    parent_quarter = matches.iloc[0]["YearQuarterConcat"]
                    d = d[d["YearQuarterConcat"] == parent_quarter]

        # 动态更新时间图标题
        if mode == "Month" and 'Q' in self.state['time']:
            self.time_title_label.set_text(f"Monthly Breakdown for {self.state['time']}")
        else:
            self.time_title_label.set_text("Performance Over Time")

        if d.empty:
            self.plot_time.update_figure(go.Figure())
            return

        # 选择分组字段（季度 or 月）
        col_group = "YearQuarterConcat" if mode == "Quarter" else "YearMonthConcat"
        # 计算每个时间段的平均分，并按时间排序
        df_bar = d.groupby(col_group)["Score"].mean().reset_index().sort_values(col_group)
        
        # 绘制柱状图（强制 x 轴为分类类型，避免 Plotly 自动解析为日期导致排序错乱）
        fig = px.bar(df_bar, x=col_group, y="Score", text_auto='.1f')
        fig.update_xaxes(type='category')
        
        # 高亮逻辑：仅当不在“季度→月”下钻过程中时，才高亮选中时间
        opacities = [1.0] * len(df_bar)
        if self.state['time'] != 'All':
            if not (mode == "Month" and 'Q' in self.state['time']):
                opacities = [1.0 if x == self.state['time'] else 0.3 for x in df_bar[col_group]]
        fig.update_traces(marker=dict(opacity=opacities))
        
        # 添加全局平均分参考线（基于当前筛选）
        d_kpi = self.get_data()
        glob_avg = d_kpi['Score'].mean() if not d_kpi.empty else 0
        fig.add_hline(y=glob_avg, line_dash="dash", line_color="red")
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), xaxis_title=None, yaxis_title="Avg Score")
        self.plot_time.update_figure(fig)

    # ── 学科平均分图（排序柱状图）────────────────────────────────────────────────
    def render_subject_chart(self):
        """
        渲染学科平均分图。
        - 按平均分降序排列（高分在左，便于识别优劣学科）
        - 高亮当前选中学科
        - 添加全局平均分参考线
        """
        d = self.get_data(ignore_subject=True)
        if d.empty:
            self.plot_subject.update_figure(go.Figure())
            return
            
        # 按学科分组计算平均分，并降序排序
        df_bar = d.groupby("SubjectName")["Score"].mean().reset_index().sort_values("Score", ascending=False)
        fig = px.bar(df_bar, x="SubjectName", y="Score", text_auto='.1f')
        
        # 高亮当前选中学科
        if self.state['subject'] != 'All':
            opacities = [1.0 if x == self.state['subject'] else 0.3 for x in df_bar["SubjectName"]]
            fig.update_traces(marker=dict(opacity=opacities))
            
        # 全局平均参考线
        d_kpi = self.get_data()
        glob_avg = d_kpi['Score'].mean() if not d_kpi.empty else 0
        fig.add_hline(y=glob_avg, line_dash="dash", line_color="red")
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), xaxis_title=None, yaxis_title="Avg Score")
        self.plot_subject.update_figure(fig)

    # ── 主更新入口：统一刷新所有组件 ─────────────────────────────────────────────
    def update_dashboard(self):
        """调度所有渲染函数，实现全局联动刷新"""
        # 更新顶部状态标签，显示当前筛选条件
        self.status_label.set_text(
            f"Filters | Grade: {self.state['grade']} | Level: {self.state['level']} | "
            f"Time: {self.state['time']} | Sub: {self.state['subject']}"
        )
        # 依次刷新所有组件
        self.render_kpis()
        self.render_grade_chart()
        self.render_level_chart()
        self.render_time_chart()
        self.render_subject_chart()

    # ── 事件处理器：响应用户交互 ─────────────────────────────────────────────────
    def reset_filters(self):
        """重置所有筛选器到默认状态"""
        self.state.update({
            'grade': 'All', 'level': 'All', 'time': 'All',
            'subject': 'All', 'view_mode': 'Quarter'
        })
        self.view_toggle.value = 'Quarter'  # 同步 UI 控件
        self.update_dashboard()

    def handle_click_grade(self, e):
        """处理 Grade 饼图点击事件"""
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['label']  # 获取点击的等级（如 'B'）
            # 切换逻辑：若已选中则取消，否则选中
            self.state['grade'] = 'All' if self.state['grade'] == clicked else clicked
            self.update_dashboard()
            
    def handle_click_level(self, e):
        """处理 Level 饼图点击事件"""
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['label']  # 如 'Grade 9'
            self.state['level'] = 'All' if self.state['level'] == clicked else clicked
            self.update_dashboard()

    def handle_click_time(self, e):
        """处理时间趋势图点击事件（支持下钻）"""
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['x']
            # Plotly 可能返回完整日期字符串（如 "2023-01-01"），需截断为 "2023-01"
            if isinstance(clicked, str) and len(clicked) > 7 and clicked[4] == '-':
                clicked = clicked[:7]
                
            # 下钻逻辑：点击季度 → 切换到月视图并筛选该季度
            if self.state['view_mode'] == 'Quarter':
                self.state['time'] = clicked
                self.state['view_mode'] = 'Month'
                self.view_toggle.value = 'Month'  # 同步 Toggle 控件
            else:
                # 月视图：切换选中/取消
                self.state['time'] = 'All' if self.state['time'] == clicked else clicked
            self.update_dashboard()

    def handle_click_subject(self, e):
        """处理学科柱状图点击事件"""
        if e.args and 'points' in e.args:
            clicked = e.args['points'][0]['x']  # 学科名称
            self.state['subject'] = 'All' if self.state['subject'] == clicked else clicked
            self.update_dashboard()

    # ── UI 构建：声明式创建界面并绑定事件 ─────────────────────────────────────────
    def build(self):
        # 添加自定义 CSS 样式
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

        # ── 顶部标题栏 ───────────────────────────────────────────────────────────
        with ui.row().classes('w-full items-center justify-between mb-4'):
            ui.label('Education Performance Analysis (Class-Based Best Practice)').classes('text-2xl font-bold text-gray-800')
            self.status_label = ui.label()  # 显示当前筛选状态
            ui.button('Reset All Filters', on_click=self.reset_filters).classes('bg-gray-500 text-white')

        # ── KPI 行：4 列网格布局 ─────────────────────────────────────────────────
        with ui.grid(columns=4).classes('w-full gap-4 mb-6'):
            # 平均分
            with ui.card().classes('card-purple'):
                ui.label('Average Score').classes('kpi-title')
                self.kpi_avg = ui.label('0.00').classes('kpi-value')
            # 加权平均
            with ui.card().classes('card-purple'):
                ui.label('Weighted Avg').classes('kpi-title')
                self.kpi_weighted = ui.label('0.00%').classes('kpi-value')
            # 通过率
            with ui.card():
                ui.label('Pass Rate').classes('text-green-600 font-medium')
                self.kpi_pass = ui.label('0.00%').classes('text-green-600 text-3xl font-bold')
            # 满分率
            with ui.card():
                ui.label('Perfect Scores').classes('text-blue-600 font-medium')
                self.kpi_perfect = ui.label('0.0%').classes('text-blue-600 text-3xl font-bold')

        # ── 第一行图表：成绩 + 年级（2 列）────────────────────────────────────────
        with ui.grid(columns=2).classes('w-full gap-6 mb-6'):
            # 成绩等级分布
            with ui.card().classes('w-full h-80'):
                ui.label('Grade Distribution').classes('font-bold text-gray-700 mb-2')
                self.plot_grade = ui.plotly({}).classes('w-full h-full')
                # 绑定点击事件 → 自动捕获 self 实例
                self.plot_grade.on('plotly_click', self.handle_click_grade)

            # 年级分布
            with ui.card().classes('w-full h-80'):
                ui.label('Level Distribution').classes('font-bold text-gray-700 mb-2')
                self.plot_level = ui.plotly({}).classes('w-full h-full')
                self.plot_level.on('plotly_click', self.handle_click_level)

        # ── 第二行图表：时间 + 学科（2 列）────────────────────────────────────────
        with ui.grid(columns=2).classes('w-full gap-6'):
            # 时间趋势图（含粒度切换）
            with ui.card().classes('w-full h-96'):
                with ui.row().classes('w-full items-center justify-between'):
                    self.time_title_label = ui.label('Performance Over Time').classes('font-bold text-gray-700')
                    # Toggle 控件：切换 Quarter/Month
                    self.view_toggle = ui.toggle(
                        ['Quarter', 'Month'], value='Quarter',
                        on_change=lambda e: [
                            self.state.update({'view_mode': e.value}),  # 更新状态
                            self.update_dashboard()                      # 刷新图表
                        ]
                    ).props('no-caps')
                self.plot_time = ui.plotly({}).classes('w-full h-full')
                self.plot_time.on('plotly_click', self.handle_click_time)

            # 学科平均分
            with ui.card().classes('w-full h-96'):
                ui.label('Score by Subject (Click to Filter)').classes('font-bold text-gray-700 mb-2')
                self.plot_subject = ui.plotly({}).classes('w-full h-full')
                self.plot_subject.on('plotly_click', self.handle_click_subject)

        # 初始化：首次渲染所有组件
        self.update_dashboard()

# ┌──────────────────────────────────────────────────────────────────────────────┐
# │ 3. ENTRY POINT: 页面路由与实例化                                              │
# │                                                                              │
# │ ★ 多用户安全核心：                                                           │
# │   - @ui.page('/') 定义根路径                                                 │
# │   - 每次新用户访问或刷新页面，都会调用 index()                               │
# │   - 在 index() 中创建 **全新 Dashboard() 实例**                              │
# │   - 因此，每个 HTTP 会话（WebSocket 连接）拥有完全独立的状态和 UI             │
# └──────────────────────────────────────────────────────────────────────────────┘

@ui.page('/')
def index():
    # 创建新 Dashboard 实例（每个用户独立）
    dashboard = Dashboard()
    # 构建 UI 并绑定事件
    dashboard.build()

# 启动应用
ui.run(title="Education Dashboard Best Practice", port=8080)