<script setup>
import { MemoryBook } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
</script>

<template>
  <div class="help-content">
    <h2 class="help-title flex items-center gap-2">
      <svg width="24" height="24" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryBook" /></svg>
      {{ t('help.title') }}
    </h2>
    
    <div class="markdown-body">
      <h3>{{ t('help.section1.title') }}</h3>
      <p>{{ t('help.section1.intro') }}</p>
      
      <h4>{{ t('help.section1.fractal.title') }}</h4>
      <ul>
        <li v-html="t('help.section1.fractal.top')"></li>
        <li v-html="t('help.section1.fractal.bottom')"></li>
        <li v-html="t('help.section1.fractal.meaning')"></li>
      </ul>
      
      <h4>{{ t('help.section1.bi.title') }}</h4>
      <ul>
        <li v-html="t('help.section1.bi.def')"></li>
        <li v-html="t('help.section1.bi.comp')"></li>
        <li v-html="t('help.section1.bi.strict')"></li>
        <li v-html="t('help.section1.bi.visual')"></li>
      </ul>
      
      <h4>{{ t('help.section1.seg.title') }}</h4>
      <ul>
        <li v-html="t('help.section1.seg.def')"></li>
        <li v-html="t('help.section1.seg.visual')"></li>
      </ul>
      
      <h4>{{ t('help.section1.center.title') }}</h4>
      <ul>
        <li v-html="t('help.section1.center.def')"></li>
        <li v-html="t('help.section1.center.visual')"></li>
        <li v-html="t('help.section1.center.meaning')"></li>
      </ul>
      
      <h3>{{ t('help.section2.title') }}</h3>
      <p>{{ t('help.section2.intro') }}</p>
      
      <table class="help-table">
        <thead>
          <tr>
            <th>{{ t('help.section2.table.type') }}</th>
            <th>{{ t('help.section2.table.name') }}</th>
            <th>{{ t('help.section2.table.desc') }}</th>
            <th>{{ t('help.section2.table.risk') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>{{ t('help.section2.table.t1.type') }}</strong></td>
            <td><strong>{{ t('help.section2.table.t1.name') }}</strong></td>
            <td>{{ t('help.section2.table.t1.desc') }}</td>
            <td>{{ t('help.section2.table.t1.risk') }}</td>
          </tr>
          <tr>
            <td><strong>{{ t('help.section2.table.t2.type') }}</strong></td>
            <td><strong>{{ t('help.section2.table.t2.name') }}</strong></td>
            <td>{{ t('help.section2.table.t2.desc') }}</td>
            <td>{{ t('help.section2.table.t2.risk') }}</td>
          </tr>
          <tr>
            <td><strong>{{ t('help.section2.table.t3.type') }}</strong></td>
            <td><strong>{{ t('help.section2.table.t3.name') }}</strong></td>
            <td>{{ t('help.section2.table.t3.desc') }}</td>
            <td>{{ t('help.section2.table.t3.risk') }}</td>
          </tr>
          <tr>
            <td><strong>{{ t('help.section2.table.t2s.type') }}</strong></td>
            <td><strong>{{ t('help.section2.table.t2s.name') }}</strong></td>
            <td>{{ t('help.section2.table.t2s.desc') }}</td>
            <td>{{ t('help.section2.table.t2s.risk') }}</td>
          </tr>
        </tbody>
      </table>
      
      <h3>{{ t('help.section3.title') }}</h3>
      <p v-html="t('help.section3.intro')"></p>
      <ul>
        <li>{{ t('help.section3.desc1') }}</li>
        <li>{{ t('help.section3.desc2') }}</li>
        <li v-html="t('help.section3.score')"></li>
      </ul>
      
      <h3>{{ t('help.section4.title') }}</h3>
      <ul>
        <li><span v-html="t('help.section4.step')"></span>
          <ul>
            <li v-html="t('help.section4.stepDesc')"></li>
          </ul>
        </li>
        <li><span v-html="t('help.section4.strict')"></span>
          <ul>
            <li v-html="t('help.section4.strictDesc')"></li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.help-content {
  padding: 2rem;
  max-width: 900px;
  margin: 0 auto;
  background-color: white;
  border-radius: 0.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.help-title {
  color: #1f2937;
  margin-bottom: 2rem;
  border-bottom: 2px solid #e5e7eb;
  padding-bottom: 1rem;
}

.markdown-body h3 {
  color: #374151;
  margin-top: 1.5rem;
  margin-bottom: 1rem;
  font-size: 1.25rem;
  font-weight: 600;
}

.markdown-body h4 {
  color: #4b5563;
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  font-size: 1rem;
  font-weight: 600;
}

.markdown-body p {
  color: #6b7280;
  line-height: 1.6;
  margin-bottom: 1rem;
}

.markdown-body ul {
  padding-left: 1.5rem;
  margin-bottom: 1rem;
  color: #6b7280;
}

.markdown-body li {
  margin-bottom: 0.5rem;
}

.help-table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.5rem 0;
  font-size: 0.9rem;
}

.help-table th, .help-table td {
  border: 1px solid #d1d5db;
  padding: 0.75rem;
  text-align: left;
}

.help-table th {
  background-color: #f9fafb;
  font-weight: 600;
  color: #374151;
}

.help-table td {
  color: #4b5563;
}
</style>
