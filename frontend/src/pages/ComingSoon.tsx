import { Card } from "../components";

export default function ComingSoon({ title, text }: { title: string; text: string }) {
  return (
    <>
      <h1>{title}</h1>
      <Card>
        <p className="muted" style={{ margin: 0 }}>{text}</p>
      </Card>
    </>
  );
}
