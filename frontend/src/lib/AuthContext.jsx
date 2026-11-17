import { createContext, useContext, useState } from 'react'
const AuthContext = createContext(null)
export function AuthProvider({ children }) {
  const [session, setSession] = useState({ token: null, user: null })
  return <AuthContext.Provider value={{ ...session, login: (token, user) => setSession({ token, user }), logout: () => setSession({ token: null, user: null }) }}>{children}</AuthContext.Provider>
}
export function useAuth() { return useContext(AuthContext) }
