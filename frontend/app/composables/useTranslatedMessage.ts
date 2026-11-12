import { computed, shallowRef } from 'vue'

// Keep the translation expression, so feedback follows a later language change.
export function useTranslatedMessage() {
  const render = shallowRef<() => string>(() => '')
  return {
    text: computed(() => render.value()),
    set: (value: () => string) => { render.value = value },
    clear: () => { render.value = () => '' },
  }
}
