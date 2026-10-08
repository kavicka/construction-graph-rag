import {
  Activity01Icon,
  BubbleChatIcon,
  CheckListIcon,
  DashboardSquare01Icon,
  File02Icon,
  FileImportIcon,
  HierarchyIcon,
  Mic01Icon,
  Settings02Icon,
  TaskDaily01Icon,
  UserGroupIcon,
  UserSettings01Icon,
} from '@hugeicons/core-free-icons'
import type { IconSvgElement } from '@hugeicons/react'

export interface NavItem {
  to: string
  label: string
  icon: IconSvgElement
  /** aktívna len na presnej ceste — /tasks nesmie svietiť na /tasks/import */
  exact?: boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export function navGroups({ isAdmin }: { isAdmin: boolean }): NavGroup[] {
  const groups: NavGroup[] = [
    { label: 'Prehľad', items: [{ to: '/status', label: 'Stav', icon: DashboardSquare01Icon }] },
  ]
  if (isAdmin) {
    groups.push({
      label: 'Firma',
      items: [
        { to: '/reports', label: 'Denné výpisy', icon: TaskDaily01Icon },
        { to: '/tasks', label: 'Úlohy', icon: CheckListIcon, exact: true },
        { to: '/tasks/import', label: 'Import úloh', icon: FileImportIcon },
        { to: '/attendance', label: 'Dochádzka', icon: Mic01Icon },
        { to: '/team', label: 'Tím a stavby', icon: UserGroupIcon },
      ],
    })
  }
  groups.push({
    label: 'Znalosti',
    items: [
      { to: '/chat', label: 'Chat', icon: BubbleChatIcon },
      { to: '/documents', label: 'Dokumenty', icon: File02Icon },
      { to: '/graph', label: 'Graf', icon: HierarchyIcon },
    ],
  })
  groups.push({
    label: 'Systém',
    items: [
      ...(isAdmin
        ? [
            { to: '/users', label: 'Používatelia', icon: UserSettings01Icon },
            { to: '/traces', label: 'Traces', icon: Activity01Icon },
          ]
        : []),
      { to: '/settings', label: 'Nastavenia', icon: Settings02Icon },
    ],
  })
  return groups
}
