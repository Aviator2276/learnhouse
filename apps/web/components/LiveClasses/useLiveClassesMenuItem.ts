'use client'
// Fork: the "Live classes" entry for the org menu (desktop header and phone
// tab bar), shown to signed-in users when plugNmeet is configured.
import { useTranslation } from 'react-i18next'
import { VideoCamera } from '@phosphor-icons/react'
import { getUriWithOrg } from '@services/config/config'
import { useLiveClassContext, useLiveClassesConfig } from './shared'

export function useLiveClassesMenuItems(orgslug: string) {
  const { t } = useTranslation()
  const config = useLiveClassesConfig()
  const { authenticated } = useLiveClassContext()
  if (!config?.enabled || !authenticated) return []
  return [
    {
      key: 'fork-live-classes',
      // Short like upstream's own menu labels ("Live"), so it fits the header.
      label: t('live_classes.nav', { defaultValue: 'Live' }),
      Icon: VideoCamera,
      href: getUriWithOrg(orgslug, '/live-classes'),
      external: false,
    },
  ]
}
