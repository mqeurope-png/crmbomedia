export function ErrorState({ title, message }: Readonly<{ title: string; message: string }>) {
  return (
    <div className="error-state" role="alert">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}
