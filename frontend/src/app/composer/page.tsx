import { redirect } from "next/navigation";

export default function ComposerIndexPage(): never {
  redirect("/composer/canvas");
}
