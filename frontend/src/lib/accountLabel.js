export function accountLabel(user) {
  if (user?.role === 'OWNER') return 'ShelfSync Owner · OWNER'

  const account = user?.email?.split('@')[0]?.toLowerCase()
  if (user?.role === 'EMPLOYEE' && account === 'employee1') return 'Employee1 · EMPLOYEE'
  if (user?.role === 'EMPLOYEE' && account === 'employee2') return 'Employee2 · EMPLOYEE'

  return user?.name || user?.email
}
