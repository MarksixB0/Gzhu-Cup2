import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { initTheme } from './shell/store'

initTheme()

createApp(App).mount('#app')
