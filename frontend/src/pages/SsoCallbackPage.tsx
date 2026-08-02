/**
 * Where Google sends the browser back to.
 *
 * The return URL carries a one-time code that has to be exchanged for a
 * session, and `AuthenticateWithRedirectCallback` is the component that does
 * it. It renders nothing, so the splash below is what the user actually sees
 * for the moment the exchange takes.
 *
 * This route sits outside every guard on purpose: mid-exchange the user is
 * neither signed in nor signed out, and either guard would redirect away from
 * the URL holding the code — losing the sign-in that was seconds from working.
 */

import { AuthenticateWithRedirectCallback } from "@clerk/clerk-react";
import { Splash } from "../components/chrome";

export default function SsoCallbackPage() {
  return (
    <>
      <AuthenticateWithRedirectCallback
        // Clerk needs these again here: the exchange can end in a state that
        // requires more steps, and without somewhere to send the user it
        // strands them on this blank route.
        signInFallbackRedirectUrl="/"
        signUpFallbackRedirectUrl="/"
      />
      <Splash label="Finishing sign-in" />
    </>
  );
}
