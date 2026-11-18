import { createContext, useCallback, useContext, useState } from 'react'

const AuthContext = createContext(null)
const sessionKey = 'shelfsync.session'
const signedOut = { token: null, user: null }

function restoreSession() {
  try {
    const session = JSON.parse(window.sessionStorage.getItem(sessionKey))
    if (typeof session?.token === 'string' && session.token &&
        ['OWNER', 'EMPLOYEE'].includes(session.user?.role)) {
      return session
    }
  } catch {
    // An unreadable session should leave the sign-in form available.
  }
  return signedOut
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(restoreSession)

  const login = useCallback((token, user) => {
    const nextSession = { token, user }
    try {
      window.sessionStorage.setItem(sessionKey, JSON.stringify(nextSession))
    } catch {
      // Sign-in still works when the browser blocks storage.
    }
    setSession(nextSession)
  }, [])

  const logout = useCallback(() => {
    try {
      window.sessionStorage.removeItem(sessionKey)
    } catch {
      // Clear the in-memory session even when storage is unavailable.
    }
    setSession(signedOut)
  }, [])

  return <AuthContext.Provider value={{ ...session, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth() { return useContext(AuthContext) }
