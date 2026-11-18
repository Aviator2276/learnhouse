// Fork: the page email footers link to. It opens the email notification
// dialog over an otherwise empty page and returns home when it closes.
import { Metadata } from 'next'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { getOrgSeoConfig, buildPageTitle } from '@/lib/seo/utils'
import EmailNotificationsPage from '@components/EmailNotifications/EmailNotificationsPage'

type PageParams = Promise<{ orgslug: string }>

export async function generateMetadata({ params }: { params: PageParams }): Promise<Metadata> {
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  const title = buildPageTitle('Email notifications', org?.name || 'Organization', getOrgSeoConfig(org))
  return { title, robots: { index: false, follow: false, nocache: true } }
}

export default async function Page({ params }: { params: PageParams }) {
  const { orgslug } = await params
  return <EmailNotificationsPage orgslug={orgslug} />
}
