'use client'
// Fork: keeps the email notification dialog available on every page. It
// opens on the profile menu's event, and `openOnMount` is for the
// /email-notifications page that email footers link to.
import React, { useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { OPEN_EMAIL_NOTIFICATIONS_EVENT } from '@services/notifications/emailNotifications'

const EmailNotificationsDialog = dynamic(() => import('./EmailNotificationsDialog'), { ssr: false })

export default function EmailNotificationsHost({ openOnMount = false }: { openOnMount?: boolean }) {
  const session = useLHSession() as any
  const router = useRouter()
  const authenticated = session?.status === 'authenticated'
  // From the profile menu: opened by the event, and the dialog's code is
  // only fetched once someone asks for it.
  const [requested, setRequested] = useState(false)
  const [open, setOpen] = useState(false)
  // On the page: open as soon as the session is known, until it is closed.
  const [closed, setClosed] = useState(false)

  useEffect(() => {
    if (openOnMount) return
    const onOpen = () => {
      setRequested(true)
      setOpen(true)
    }
    window.addEventListener(OPEN_EMAIL_NOTIFICATIONS_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EMAIL_NOTIFICATIONS_EVENT, onOpen)
  }, [openOnMount])

  if (!authenticated || !(openOnMount || requested)) return null
  return (
    <EmailNotificationsDialog
      open={openOnMount ? !closed : open}
      onOpenChange={(next) => {
        if (!openOnMount) {
          setOpen(next)
        } else if (!next) {
          setClosed(true)
          router.replace('/')
        }
      }}
    />
  )
}
