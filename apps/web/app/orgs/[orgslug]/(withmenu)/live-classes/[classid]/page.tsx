// Fork: one plugNmeet live class.
import { Metadata } from 'next'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { getOrgSeoConfig, buildPageTitle } from '@/lib/seo/utils'
import LiveClassPage from '@components/LiveClasses/LiveClassPage'

type PageParams = Promise<{ orgslug: string; classid: string }>

export async function generateMetadata({ params }: { params: PageParams }): Promise<Metadata> {
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  const title = buildPageTitle('Live class', org?.name || 'Organization', getOrgSeoConfig(org))
  return { title, robots: { index: false, follow: false, nocache: true } }
}

export default async function Page({ params }: { params: PageParams }) {
  const { orgslug, classid } = await params
  return <LiveClassPage orgslug={orgslug} classId={classid} />
}
