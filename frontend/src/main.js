import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import VueECharts from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import {
  LineChart, BarChart, HeatmapChart, ScatterChart,
} from 'echarts/charts'
import {
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent,
  ToolboxComponent,
} from 'echarts/components'

import App from './App.vue'
import router from './router'

use([
  CanvasRenderer,
  LineChart, BarChart, HeatmapChart, ScatterChart,
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent,
  ToolboxComponent,
])

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.component('v-chart', VueECharts)

for (const [key, icon] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, icon)
}

app.mount('#app')
