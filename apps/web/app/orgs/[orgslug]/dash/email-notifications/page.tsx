// Fork: organization settings for community and assignment emails.
import { Metadata } from 'next'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import EmailNotificationSettings from '@components/EmailNotifications/EmailNotificationSettings'

type PageParams = Promise<{ orgslug: string }>

export async function generateMetadata({ params }: { params: PageParams }): Promise<Metadata> {
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  return {
    title: 'Email notifications | ' + (org?.name || 'Organization'),
    robots: { index: false, follow: false },
  }
}

export default function Page() {
  return <EmailNotificationSettings />
}
