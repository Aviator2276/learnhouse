// Fork: plugNmeet live classes.
import { Metadata } from 'next'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { getOrgSeoConfig, buildPageTitle } from '@/lib/seo/utils'
import LiveClassesPage from '@components/LiveClasses/LiveClassesPage'

type PageParams = Promise<{ orgslug: string }>

export async function generateMetadata({ params }: { params: PageParams }): Promise<Metadata> {
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  const title = buildPageTitle('Live classes', org?.name || 'Organization', getOrgSeoConfig(org))
  // Members only: nothing here is worth indexing.
  return { title, robots: { index: false, follow: false, nocache: true } }
}

export default async function Page({ params }: { params: PageParams }) {
  const { orgslug } = await params
  return <LiveClassesPage orgslug={orgslug} />
}
