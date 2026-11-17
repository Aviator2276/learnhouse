'use client'
// Fork: /email-notifications. Signed-in members get the dialog; everyone
// else is asked to sign in first.
import React from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { EnvelopeSimple } from '@phosphor-icons/react'
import GeneralWrapperStyled from '@components/Objects/StyledElements/Wrappers/GeneralWrapper'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getUriWithOrg } from '@services/config/config'
import EmailNotificationsHost from './EmailNotificationsHost'

export default function EmailNotificationsPage({ orgslug }: { orgslug: string }) {
  const { t } = useTranslation()
  const session = useLHSession() as any
  const signedOut = session?.status === 'unauthenticated'

  return (
    <GeneralWrapperStyled>
      <div className="mx-auto max-w-md py-16 text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
          <EnvelopeSimple size={22} />
        </span>
        <h1 className="mt-4 text-xl font-semibold text-gray-900">
          {t('email_notifications.title', { defaultValue: 'Email notifications' })}
        </h1>
        {signedOut ? (
          <>
            <p className="mt-1 text-sm text-gray-500">
              {t('email_notifications.sign_in', { defaultValue: 'Sign in to change which emails you get.' })}
            </p>
            <Link
              href={getUriWithOrg(orgslug, '/login')}
              className="mt-5 inline-flex rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
            >
              {t('auth.login')}
            </Link>
          </>
        ) : (
          <p className="mt-1 text-sm text-gray-500">
            {t('email_notifications.page_hint', {
              defaultValue: 'You can also open these settings from your profile menu at any time.',
            })}
          </p>
        )}
      </div>
      <EmailNotificationsHost openOnMount />
    </GeneralWrapperStyled>
  )
}
