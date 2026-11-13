'use client'
// Fork: "Live classes" box in the course page sidebar. Lists the course's
// upcoming classes; course editors can schedule from here.
import React, { useState } from 'react'
import Link from 'next/link'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { CalendarPlus, Video } from 'lucide-react'
import Modal from '@components/Objects/StyledElements/Modal/Modal'
import { getUriWithOrg } from '@services/config/config'
import { getLiveClassPermissions, listLiveClasses } from '@services/plugnmeet/liveClasses'
import ScheduleClassForm from './ScheduleClassForm'
import { liveClassKeys } from './LiveClassesPage'
import { JoinButton, StatusBadge, classHref, relativeStart, useFormatters, useLiveClassContext, useLiveClassesConfig, useNow } from './shared'

export default function CourseLiveClasses({ courseUuid, courseName, orgslug }: { courseUuid: string; courseName?: string; orgslug: string }) {
  const { t } = useTranslation()
  const { orgId, token } = useLiveClassContext()
  const config = useLiveClassesConfig()
  const queryClient = useQueryClient()
  const fmt = useFormatters()
  const now = useNow()
  const [scheduling, setScheduling] = useState(false)
  const ready = !!config?.enabled && !!token && orgId > 0 && !!courseUuid

  const list = useQuery({
    queryKey: liveClassKeys.list(orgId, 'upcoming', courseUuid),
    queryFn: () => listLiveClasses(orgId, 'upcoming', token, courseUuid, 5),
    enabled: ready,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })
  const perms = useQuery({
    queryKey: liveClassKeys.perms(orgId, courseUuid),
    queryFn: () => getLiveClassPermissions(orgId, token, courseUuid),
    enabled: ready,
    staleTime: 60_000,
    retry: false,
  })

  const classes = list.data || []
  const canSchedule = !!perms.data?.can_schedule
  if (!ready || list.isError || (classes.length === 0 && !canSchedule)) return null

  return (
    <section
      className="bg-white shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40 rounded-lg overflow-hidden"
      aria-labelledby="course-live-classes"
    >
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <h2 id="course-live-classes" className="flex items-center gap-2 text-sm font-semibold text-gray-900">
          <Video className="h-4 w-4 text-gray-400" />
          {t('live_classes.title', { defaultValue: 'Live classes' })}
        </h2>
        <Link href={getUriWithOrg(orgslug, '/live-classes')} className="text-xs font-medium text-gray-500 hover:text-gray-900">
          {t('live_classes.view_all', { defaultValue: 'View all' })}
        </Link>
      </div>
      {classes.length === 0 ? (
        <p className="px-4 py-4 text-sm text-gray-400">{t('live_classes.empty_course', { defaultValue: 'No live classes scheduled.' })}</p>
      ) : (
        <ul className="divide-y divide-gray-50">
          {classes.map((c) => (
            <li key={c.class_uuid} className="space-y-2 px-4 py-3">
              <Link href={classHref(orgslug, c)} className="block group">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-gray-900 group-hover:underline underline-offset-2">{c.title}</span>
                  {c.status === 'live' && <StatusBadge status="live" />}
                </div>
                <p className="text-xs text-gray-500">
                  {fmt.day.format(new Date(c.starts_at))} · {fmt.time.format(new Date(c.starts_at))}
                  {c.status === 'scheduled' && <span className="text-gray-400"> ({relativeStart(fmt, c.starts_at, now)})</span>}
                </p>
              </Link>
              {(c.can_join || (c.can_moderate && c.can_start)) && <JoinButton liveClass={c} size="sm" />}
            </li>
          ))}
        </ul>
      )}
      {canSchedule && (
        <div className="border-t border-gray-100 px-4 py-3">
          <Modal
            isDialogOpen={scheduling}
            onOpenChange={setScheduling}
            minWidth="sm"
            dialogTitle={t('live_classes.schedule_title', { defaultValue: 'Schedule a live class' })}
            dialogDescription={
              courseName
                ? t('live_classes.schedule_course_description', { course: courseName, defaultValue: `For learners of ${courseName}.` })
                : undefined
            }
            dialogContent={
              <ScheduleClassForm
                orgId={orgId}
                courseUuid={courseUuid}
                onCancel={() => setScheduling(false)}
                onDone={(created) => {
                  setScheduling(false)
                  toast.success(
                    created.length > 1
                      ? t('live_classes.scheduled_many', { count: created.length, defaultValue: `${created.length} classes scheduled` })
                      : t('live_classes.scheduled_one', { defaultValue: 'Class scheduled' })
                  )
                  queryClient.invalidateQueries({ queryKey: liveClassKeys.all })
                }}
              />
            }
            dialogTrigger={
              <button type="button" className="flex w-full items-center justify-center gap-2 rounded-lg py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-900">
                <CalendarPlus className="h-4 w-4" />
                {t('live_classes.actions.schedule_class', { defaultValue: 'Schedule class' })}
              </button>
            }
          />
        </div>
      )}
    </section>
  )
}
